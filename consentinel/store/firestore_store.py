"""Firestore implementation of the Store contract.

Firestore, not SQLite: Cloud Run's filesystem is ephemeral and instances are
replaced, so a SQLite registry would not survive between requests. `schema.sql`
remains the logical model; this is the physical one.

Two properties worth knowing before reading further:

* **A finding's document id is its `url_hash`.** That gives upsert idempotency
  for free — re-running a sweep updates the document in place and refreshes
  `last_checked`, and it is structurally impossible to end up with two
  documents for one URL.
* **`audit_log` is append-only.** This class exposes `append_audit` and
  `list_audit` and nothing else. The `Store` ABC omits update and delete
  deliberately: for a compliance product the defensible trail *is* the value,
  and removing the capability beats remembering not to use it.

Joins we would have written in SQL happen in Python. The data is small — a
sweep touches a few hundred documents at most.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Iterable, Optional

from google.cloud import firestore

from consentinel.store.base import (
    Asset,
    AuditEvent,
    ClearanceState,
    Consent,
    Dossier,
    Finding,
    FindingStatus,
    Performer,
    Store,
    Verdict,
)
from consentinel.store.codec import coerce_dt, from_doc, to_doc, utcnow

PERFORMERS = "performers"
CONSENTS = "consents"
FINDINGS = "findings"
ASSETS = "assets"
DOSSIERS = "dossiers"
AUDIT_LOG = "audit_log"


class FirestoreStore(Store):
    """
    Args:
        project: GCP project id. Defaults to GOOGLE_CLOUD_PROJECT.
        prefix:  Collection-name prefix. Tests pass one so they never touch the
                 real collections; production leaves it empty.
        client:  Inject a client for testing. Set FIRESTORE_EMULATOR_HOST to
                 point the default client at the emulator instead.
    """

    def __init__(
        self,
        project: Optional[str] = None,
        prefix: str = "",
        client: Optional["firestore.Client"] = None,
    ) -> None:
        self.prefix = prefix
        self.db = client or firestore.Client(
            project=project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        )

    # ------------------------------------------------------------------ utils

    def _col(self, name: str):
        return self.db.collection(f"{self.prefix}{name}")

    @staticmethod
    def _docs(query) -> Iterable[dict[str, Any]]:
        for snap in query.stream():
            data = snap.to_dict() or {}
            data.setdefault("id", snap.id)
            yield data

    # ------------------------------------------------------------- performers

    def upsert_performer(self, performer: Performer) -> Performer:
        if performer.created_at is None:
            performer.created_at = utcnow()
        self._col(PERFORMERS).document(performer.id).set(to_doc(performer))
        return performer

    def get_performer(self, performer_id: str) -> Optional[Performer]:
        snap = self._col(PERFORMERS).document(performer_id).get()
        if not snap.exists:
            return None
        return from_doc(Performer, snap.to_dict() or {})

    def list_performers(self) -> list[Performer]:
        return [from_doc(Performer, d) for d in self._docs(self._col(PERFORMERS))]

    # --------------------------------------------------------------- consents

    def upsert_consent(self, consent: Consent) -> Consent:
        if consent.created_at is None:
            consent.created_at = utcnow()
        consent.valid_from = coerce_dt(consent.valid_from)
        consent.valid_to = coerce_dt(consent.valid_to)
        self._col(CONSENTS).document(consent.id).set(to_doc(consent))
        return consent

    def list_consents(self, performer_id: str) -> list[Consent]:
        q = self._col(CONSENTS).where(
            filter=firestore.FieldFilter("performer_id", "==", performer_id)
        )
        return [from_doc(Consent, d) for d in self._docs(q)]

    # --------------------------------------------------------------- findings

    def upsert_finding(self, finding: Finding) -> Finding:
        """Idempotent on `url_hash`, which is the document id.

        A second sweep of the same URL refreshes `last_checked` and leaves
        `first_seen` alone — so "when did we first see this" survives every
        re-run, which is what makes scheduled monitoring meaningful.
        """
        if not finding.url_hash:
            raise ValueError("finding.url_hash is required: it is the document id")

        ref = self._col(FINDINGS).document(finding.url_hash)
        existing = ref.get()
        now = utcnow()

        if existing.exists:
            prior = existing.to_dict() or {}
            finding.first_seen = coerce_dt(
                from_doc(Finding, prior).first_seen or finding.first_seen or now
            )
        elif finding.first_seen is None:
            finding.first_seen = now

        finding.last_checked = now
        ref.set(to_doc(finding))
        return finding

    def get_finding(self, finding_id: str) -> Optional[Finding]:
        """Accepts either the `url_hash` (the document id) or the logical `id`."""
        snap = self._col(FINDINGS).document(finding_id).get()
        if snap.exists:
            return from_doc(Finding, snap.to_dict() or {})
        q = self._col(FINDINGS).where(
            filter=firestore.FieldFilter("id", "==", finding_id)
        ).limit(1)
        for d in self._docs(q):
            return from_doc(Finding, d)
        return None

    def list_findings(
        self,
        performer_id: Optional[str] = None,
        verdict: Optional[Verdict] = None,
        status: Optional[FindingStatus] = None,
    ) -> list[Finding]:
        q = self._col(FINDINGS)
        if performer_id:
            q = q.where(filter=firestore.FieldFilter("performer_id", "==", performer_id))
        if verdict:
            q = q.where(filter=firestore.FieldFilter("verdict", "==", verdict.value))
        if status:
            q = q.where(filter=firestore.FieldFilter("status", "==", status.value))
        return [from_doc(Finding, d) for d in self._docs(q)]

    # ----------------------------------------------------------------- assets

    def upsert_asset(self, asset: Asset) -> Asset:
        if asset.created_at is None:
            asset.created_at = utcnow()
        self._col(ASSETS).document(asset.id).set(to_doc(asset))
        return asset

    def list_assets(
        self, production_id: str, state: Optional[ClearanceState] = None
    ) -> list[Asset]:
        q = self._col(ASSETS).where(
            filter=firestore.FieldFilter("production_id", "==", production_id)
        )
        if state:
            q = q.where(
                filter=firestore.FieldFilter("clearance_state", "==", state.value)
            )
        return [from_doc(Asset, d) for d in self._docs(q)]

    # --------------------------------------------------------------- dossiers

    def put_dossier(self, dossier: Dossier) -> Dossier:
        if dossier.generated_at is None:
            dossier.generated_at = utcnow()
        self._col(DOSSIERS).document(dossier.id).set(to_doc(dossier))
        return dossier

    def get_dossier(self, finding_id: str) -> Optional[Dossier]:
        q = self._col(DOSSIERS).where(
            filter=firestore.FieldFilter("finding_id", "==", finding_id)
        ).limit(1)
        for d in self._docs(q):
            return from_doc(Dossier, d)
        return None

    # -------------------------------------------------------------- audit log

    def append_audit(self, event: AuditEvent) -> None:
        """Append only. There is no update and no delete, by design."""
        if event.ts is None:
            event.ts = utcnow()
        doc_id = event.id or uuid.uuid4().hex
        event.id = doc_id
        self._col(AUDIT_LOG).document(doc_id).create(to_doc(event))

    def list_audit(
        self, subject_type: Optional[str] = None, subject_id: Optional[str] = None
    ) -> list[AuditEvent]:
        q = self._col(AUDIT_LOG)
        if subject_type:
            q = q.where(filter=firestore.FieldFilter("subject_type", "==", subject_type))
        if subject_id:
            q = q.where(filter=firestore.FieldFilter("subject_id", "==", subject_id))
        events = [from_doc(AuditEvent, d) for d in self._docs(q)]
        events.sort(key=lambda e: e.ts or utcnow())
        return events

    # ----------------------------------------------------------------- testing

    def purge(self) -> int:
        """Delete every document under this prefix. Refuses to run unprefixed.

        Exists so tests can clean up after themselves. The guard is the whole
        point: an unprefixed purge would delete the real registry and the audit
        trail, which is exactly the operation the rest of this class is built to
        make impossible.
        """
        if not self.prefix:
            raise RuntimeError(
                "purge() refuses to run without a collection prefix — "
                "it would delete the real registry and audit log"
            )
        n = 0
        for name in (PERFORMERS, CONSENTS, FINDINGS, ASSETS, DOSSIERS, AUDIT_LOG):
            for snap in self._col(name).stream():
                snap.reference.delete()
                n += 1
        return n
