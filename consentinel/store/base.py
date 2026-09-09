"""Consentinel storage contract. FROZEN — agree with your teammate before changing.

Everything above this layer codes against `Store` and `Cache`, never against a
concrete engine. That is what lets the agent side and the app side be built in
parallel, and what lets us swap SQLite for ClickHouse later without a rewrite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

class PermittedUse(str, Enum):
    VOICE_SYNTH = "voice_synth"
    FACE_REPLACE = "face_replace"
    FULL_REPLICA = "full_replica"
    ARCHIVAL_REUSE = "archival_reuse"


class Modality(str, Enum):
    VOICE = "voice"
    FACE = "face"
    PERFORMANCE = "performance"


class Verdict(str, Enum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    AMBIGUOUS = "ambiguous"


class ClearanceState(str, Enum):
    UNVERIFIED = "unverified"
    CLEARED = "cleared"
    BLOCKED = "blocked"


class FindingStatus(str, Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    DOSSIER_DRAFTED = "dossier_drafted"
    DISMISSED = "dismissed"

    # We looked up the address, found it was known-dangerous, and did not open
    # it. The verdict stays `ambiguous`, because refusing to look is not the
    # same as deciding the use was allowed.
    BLOCKED_UNSAFE = "blocked_unsafe"

    # The page contained material we must not keep a copy of. Nothing is
    # snapshotted and nothing is shown on screen; a person is told instead.
    ESCALATED_UNLAWFUL = "escalated_unlawful"


class DiscoveredVia(str, Enum):
    TEXT = "text"
    IMAGE = "image"


WORLDWIDE = "WORLDWIDE"


@dataclass(frozen=True)
class Locale:
    """Language + region. Territory is load-bearing: consent is territory-scoped,
    so locale drives both search coverage and the final verdict."""

    language: str  # ISO 639-1, e.g. "pt"
    region: str    # ISO 3166-1 alpha-2, e.g. "BR"

    def __str__(self) -> str:
        return f"{self.language}-{self.region}"


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Performer:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    reference_images: list[str] = field(default_factory=list)
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Consent:
    """A permission slip, extracted from a contract."""

    id: str
    performer_id: str
    licensee: str
    permitted_uses: list[PermittedUse] = field(default_factory=list)
    territories: list[str] = field(default_factory=list)  # alpha-2, or [WORLDWIDE]
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    compensation_trigger: Optional[str] = None
    clause_citations: list[dict[str, Any]] = field(default_factory=list)
    source_doc_ref: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Finding:
    """Suspected third-party use, discovered by the outward pipeline.

    `evidence_quote` is not optional in practice: no verdict may be written
    without a citation. Enforce that in the reconciler, not here.
    """

    id: str
    performer_id: str
    url: str
    url_hash: str
    discovered_via: DiscoveredVia
    discovered_locale: str
    target_territories: list[str] = field(default_factory=list)
    modality: Optional[Modality] = None
    is_commercial: Optional[bool] = None
    evidence_quote: Optional[str] = None
    confidence: Optional[float] = None
    verdict: Optional[Verdict] = None
    matched_consent_id: Optional[str] = None
    reasoning: Optional[str] = None
    status: FindingStatus = FindingStatus.NEW
    evidence_uri: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_checked: Optional[datetime] = None


@dataclass
class Asset:
    """One of our own deliverables, checked by the inward pipeline."""

    id: str
    production_id: str
    filename: str
    shot_code: Optional[str] = None
    content_hash: Optional[str] = None
    provenance_metadata: Optional[dict[str, Any]] = None
    vendor: Optional[str] = None
    invoice_ref: Optional[str] = None
    performer_id: Optional[str] = None
    synthetic: str = "unknown"  # yes|no|unknown
    detected_modality: Optional[Modality] = None
    clearance_state: ClearanceState = ClearanceState.UNVERIFIED
    matched_consent_id: Optional[str] = None
    reasoning: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Dossier:
    id: str
    finding_id: str
    evidence_bundle: dict[str, Any]
    draft_notice: str
    generated_at: Optional[datetime] = None


@dataclass
class AuditEvent:
    id: str
    ts: datetime
    actor: str
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    inputs: Optional[dict[str, Any]] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    output: Optional[dict[str, Any]] = None
    prompt_version: Optional[str] = None


# --------------------------------------------------------------------------
# Interfaces
# --------------------------------------------------------------------------

class Store(ABC):
    """Persistence for the registry, findings, assets and audit trail."""

    # performers
    @abstractmethod
    def upsert_performer(self, performer: Performer) -> Performer: ...

    @abstractmethod
    def get_performer(self, performer_id: str) -> Optional[Performer]: ...

    @abstractmethod
    def list_performers(self) -> list[Performer]: ...

    # consents
    @abstractmethod
    def upsert_consent(self, consent: Consent) -> Consent: ...

    @abstractmethod
    def list_consents(self, performer_id: str) -> list[Consent]: ...

    # findings — upsert is idempotent on url_hash
    @abstractmethod
    def upsert_finding(self, finding: Finding) -> Finding: ...

    @abstractmethod
    def get_finding(self, finding_id: str) -> Optional[Finding]: ...

    @abstractmethod
    def list_findings(
        self,
        performer_id: Optional[str] = None,
        verdict: Optional[Verdict] = None,
        status: Optional[FindingStatus] = None,
    ) -> list[Finding]: ...

    # assets
    @abstractmethod
    def upsert_asset(self, asset: Asset) -> Asset: ...

    @abstractmethod
    def list_assets(
        self, production_id: str, state: Optional[ClearanceState] = None
    ) -> list[Asset]: ...

    # dossiers
    @abstractmethod
    def put_dossier(self, dossier: Dossier) -> Dossier: ...

    @abstractmethod
    def get_dossier(self, finding_id: str) -> Optional[Dossier]: ...

    # audit — append only
    @abstractmethod
    def append_audit(self, event: AuditEvent) -> None: ...

    @abstractmethod
    def list_audit(
        self, subject_type: Optional[str] = None, subject_id: Optional[str] = None
    ) -> list[AuditEvent]: ...


@dataclass
class CacheEntry:
    value: Any
    fetched_at: datetime

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        return ((now or datetime.utcnow()) - self.fetched_at).total_seconds()


class Cache(ABC):
    """TTL cache for expensive, repeatable work.

    Cache these:      search results, page fetches, image matches, LLM extractions
    Never cache:      reconciler verdicts. A verdict is a function of the registry,
                      and the registry is mutable — a consent can expire or be
                      revoked, which silently invalidates a stored verdict.
                      Recompute on read; the expensive inputs are already cached.

    Two key rules:
      * `prompt_version` must be part of the key for any LLM extraction, or you
        will serve results from a prompt you already deleted.
      * `locale` must be part of the key for anything web-facing, or you will
        serve US results for a JP query and quietly corrupt territory logic.
    """

    @abstractmethod
    def get(self, key: str) -> Optional[CacheEntry]: ...

    @abstractmethod
    def put(self, key: str, value: Any, ttl_seconds: int) -> None: ...


class EvidenceStore(ABC):
    """Immutable, no-TTL snapshots for enforcement dossiers.

    This is NOT the cache. An infringing page will be taken down — that is the
    point of a takedown — so the page text and screenshot captured at discovery
    must be preserved permanently or the dossier loses its evidence.
    """

    @abstractmethod
    def put_snapshot(self, finding_id: str, name: str, data: bytes, content_type: str) -> str:
        """Returns a durable URI."""
