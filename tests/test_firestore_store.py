"""WU-01 acceptance.

Done when: every record type round-trips with fields intact, and writing the
same finding twice leaves exactly one document.

These run against the real `consentinel` project under a throwaway collection
prefix, so they never touch the live registry. Set FIRESTORE_EMULATOR_HOST to
run them against the emulator instead — the code path is identical.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from consentinel.store.base import (
    Asset,
    AuditEvent,
    ClearanceState,
    Consent,
    DiscoveredVia,
    Dossier,
    Finding,
    FindingStatus,
    Modality,
    PermittedUse,
    Performer,
    Verdict,
)

pytest.importorskip("google.cloud.firestore")
from consentinel.store.firestore_store import FirestoreStore  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "consentinel")


@pytest.fixture(scope="module")
def store():
    s = FirestoreStore(project=PROJECT, prefix=f"test_{uuid.uuid4().hex[:8]}_")
    yield s
    s.purge()


def _performer() -> Performer:
    return Performer(
        id="perf_mira_vance",
        name="Mira Vance",
        aliases=["M. Vance", "Mira V."],
        reference_images=["gs://bucket/mira_01.jpg"],
        notes="Fictional performer used for tests.",
    )


def _consent() -> Consent:
    return Consent(
        id="cons_halcyon_2026",
        performer_id="perf_mira_vance",
        licensee="Halcyon Pictures",
        permitted_uses=[PermittedUse.VOICE_SYNTH, PermittedUse.ARCHIVAL_REUSE],
        territories=["US", "CA"],
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2028, 12, 31, tzinfo=timezone.utc),
        compensation_trigger="Per-title fee on retained synthetic voice lines.",
        clause_citations=[{"quote": "Producer may generate synthetic voice", "page": 4}],
        source_doc_ref="gs://bucket/agreement.pdf",
    )


def _finding(url_hash="f1a0c0de0001", verdict=Verdict.UNAUTHORIZED) -> Finding:
    return Finding(
        id="find_0001",
        performer_id="perf_mira_vance",
        url="https://example-marketplace.invalid/listing/mira-vance-voice",
        url_hash=url_hash,
        discovered_via=DiscoveredVia.TEXT,
        discovered_locale="en-US",
        target_territories=["US"],
        modality=Modality.VOICE,
        is_commercial=True,
        evidence_quote="Instant AI voice model of Mira Vance, unlimited commercial use.",
        confidence=0.91,
        verdict=verdict,
        reasoning="No grant covers voice_synth for this actor.",
        status=FindingStatus.NEW,
    )


# ------------------------------------------------------------ round-tripping

def test_performer_round_trips(store):
    store.upsert_performer(_performer())
    got = store.get_performer("perf_mira_vance")
    assert got is not None
    assert got.name == "Mira Vance"
    assert got.aliases == ["M. Vance", "Mira V."]
    assert got.reference_images == ["gs://bucket/mira_01.jpg"]
    assert got.created_at is not None and got.created_at.tzinfo is not None


def test_consent_round_trips_with_enums_and_dates(store):
    store.upsert_performer(_performer())
    store.upsert_consent(_consent())
    got = store.list_consents("perf_mira_vance")
    assert len(got) == 1
    c = got[0]
    assert c.permitted_uses == [PermittedUse.VOICE_SYNTH, PermittedUse.ARCHIVAL_REUSE]
    assert all(isinstance(u, PermittedUse) for u in c.permitted_uses)
    assert c.territories == ["US", "CA"]
    assert c.valid_from.year == 2026 and c.valid_to.year == 2028
    assert c.clause_citations[0]["page"] == 4


def test_finding_round_trips(store):
    store.upsert_finding(_finding())
    got = store.get_finding("f1a0c0de0001")
    assert got is not None
    assert got.verdict is Verdict.UNAUTHORIZED
    assert got.modality is Modality.VOICE
    assert got.discovered_via is DiscoveredVia.TEXT
    assert got.status is FindingStatus.NEW
    assert got.is_commercial is True
    assert got.confidence == pytest.approx(0.91)
    assert got.target_territories == ["US"]


def test_asset_round_trips(store):
    store.upsert_asset(Asset(
        id="asset_0533",
        production_id="prod_halcyon_nightfall",
        shot_code="NF_1188_VFX",
        filename="NF_1188_VFX_v07.exr",
        vendor="Cyan Alley VFX",
        performer_id="perf_mira_vance",
        synthetic="yes",
        detected_modality=Modality.FACE,
        clearance_state=ClearanceState.BLOCKED,
        reasoning="Visual likeness withheld by the grant.",
    ))
    got = store.list_assets("prod_halcyon_nightfall")
    assert len(got) == 1
    assert got[0].clearance_state is ClearanceState.BLOCKED
    assert got[0].detected_modality is Modality.FACE


def test_dossier_round_trips(store):
    store.put_dossier(Dossier(
        id="dos_0001",
        finding_id="find_0001",
        evidence_bundle={"url": "https://example.invalid", "snapshots": ["gs://x"]},
        draft_notice="Dear sir or madam,",
    ))
    got = store.get_dossier("find_0001")
    assert got is not None
    assert got.evidence_bundle["snapshots"] == ["gs://x"]
    assert got.generated_at is not None


# ----------------------------------------------------------- idempotency

def test_writing_the_same_finding_twice_leaves_one_document(store):
    h = "dedupe_hash_001"
    store.upsert_finding(_finding(url_hash=h))
    store.upsert_finding(_finding(url_hash=h, verdict=Verdict.AMBIGUOUS))

    matches = [f for f in store.list_findings() if f.url_hash == h]
    assert len(matches) == 1, "url_hash is the document id; a re-sweep must not duplicate"
    assert matches[0].verdict is Verdict.AMBIGUOUS, "the second write updates in place"


def test_first_seen_survives_a_resweep(store):
    h = "firstseen_hash_001"
    original = _finding(url_hash=h)
    original.first_seen = datetime.now(timezone.utc) - timedelta(days=3)
    store.upsert_finding(original)

    again = _finding(url_hash=h)
    again.first_seen = None
    saved = store.upsert_finding(again)

    age_days = (datetime.now(timezone.utc) - saved.first_seen).days
    assert age_days >= 3, "first_seen must survive re-sweeps or monitoring means nothing"
    assert saved.last_checked > saved.first_seen


def test_url_hash_is_required(store):
    bad = _finding()
    bad.url_hash = ""
    with pytest.raises(ValueError):
        store.upsert_finding(bad)


# ---------------------------------------------------------------- filtering

def test_findings_filter_by_verdict_and_status(store):
    store.upsert_finding(_finding(url_hash="filt_a", verdict=Verdict.UNAUTHORIZED))
    auth = _finding(url_hash="filt_b", verdict=Verdict.AUTHORIZED)
    auth.status = FindingStatus.REVIEWED
    store.upsert_finding(auth)

    unauthorized = store.list_findings(verdict=Verdict.UNAUTHORIZED)
    assert all(f.verdict is Verdict.UNAUTHORIZED for f in unauthorized)
    assert any(f.url_hash == "filt_a" for f in unauthorized)

    reviewed = store.list_findings(status=FindingStatus.REVIEWED)
    assert any(f.url_hash == "filt_b" for f in reviewed)


# ---------------------------------------------------------------- audit log

def test_audit_appends_and_reads_back_in_order(store):
    for i in range(3):
        store.append_audit(AuditEvent(
            id=f"aud_{i}", ts=datetime.now(timezone.utc) + timedelta(seconds=i),
            actor="Triage", subject_type="finding", subject_id="find_0001",
            tool_calls=[{"tool": "fetch_page", "from_cache": True, "cache_age_s": 42.0}],
            output={"confidence": 0.9}, prompt_version="v1",
        ))
    events = store.list_audit(subject_type="finding", subject_id="find_0001")
    assert len(events) == 3
    assert [e.id for e in events] == ["aud_0", "aud_1", "aud_2"]
    assert events[0].tool_calls[0]["cache_age_s"] == 42.0


def test_audit_has_no_update_or_delete_methods():
    """The capability is absent, not merely discouraged."""
    for name in ("update_audit", "delete_audit", "remove_audit", "clear_audit"):
        assert not hasattr(FirestoreStore, name)


def test_appending_the_same_audit_id_twice_is_rejected(store):
    ev = AuditEvent(id="aud_immutable", ts=datetime.now(timezone.utc), actor="Reconciler")
    store.append_audit(ev)
    with pytest.raises(Exception):
        store.append_audit(AuditEvent(id="aud_immutable",
                                      ts=datetime.now(timezone.utc), actor="tamper"))


# ------------------------------------------------------------------- safety

def test_purge_refuses_without_a_prefix():
    """No credentials needed: the guard fires before any client call."""
    s = FirestoreStore(project=PROJECT, prefix="", client=object())
    with pytest.raises(RuntimeError, match="prefix"):
        s.purge()
