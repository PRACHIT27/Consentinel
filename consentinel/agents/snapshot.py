"""WU-15 (capture half) — freeze the page at the moment of discovery.

FR-5.1. **This is not the cache.** The infringing page will be taken down —
that is the entire point of a takedown — so what we captured at discovery is
the only thing left. Immutable, no TTL, and `EvidenceStore` deliberately
exposes no delete (hard rule 9).

WU-15 is split: `consentinel/evidence/store.py` is Prachit's, this is mine. So
this module writes through the `EvidenceStore` interface and does not care
which implementation is behind it. `LocalSnapshotStore` at the bottom is a
**stopgap** so the dossier path is testable and demoable today; it lives here
rather than in `consentinel/evidence/` precisely so it cannot collide with his
file. Delete it when his lands.

**Text only, deliberately.** WU-15 says a screenshot on Cloud Run needs
headless Chromium and to ship text-only if that becomes a rabbit hole (OQ-4 is
still open). Text is the evidence that matters — a quote a human can read and
verify. `capture()` accepts a `screenshot` callable, so when the Chromium
question is answered the answer plugs in here without touching anything else.

**One page never gets snapshotted at all.** DESIGN Part III §4: when a page is
flagged as carrying material we must not keep, nothing is written — no text, no
screenshot — and the finding records the address, a hash, the classification
and the time. This is the one place the product deliberately keeps *less*
evidence, because here preserving is the harm.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from consentinel.store.base import FindingStatus
from consentinel.tools.contracts import PageSnapshot

AGENT_NAME = "Snapshot"

TEXT_NAME = "page.txt"
SCREENSHOT_NAME = "page.png"
METADATA_NAME = "metadata.json"

log = logging.getLogger("consentinel.snapshot")


class EvidenceWriter(Protocol):
    """The one `EvidenceStore` method capture needs.

    A protocol, so this module works against Prachit's implementation, a GCS
    one, or a five-line fake in a test. Note what is absent: there is no
    `delete`, here or in the ABC.
    """

    def put_snapshot(self, finding_id: str, name: str, data: bytes,
                     content_type: str) -> str: ...


Screenshot = Callable[[str], Optional[bytes]]
"""url -> PNG bytes, or None. Injected because the Cloud Run screenshot
question (OQ-4) is open and text-only is a shipped answer, not a placeholder."""


@dataclass(frozen=True)
class SnapshotRef:
    """What was frozen, where it went, and what it hashes to.

    The hashes are the point: a URI proves where the bytes are, a hash proves
    they have not changed since discovery. A dossier that cannot demonstrate
    that is a dossier somebody can argue with.
    """

    finding_id: str
    url: str
    captured_at: datetime
    text_uri: Optional[str] = None
    text_sha256: Optional[str] = None
    text_chars: int = 0
    screenshot_uri: Optional[str] = None
    screenshot_sha256: Optional[str] = None
    metadata_uri: Optional[str] = None
    withheld: bool = False
    withheld_reason: Optional[str] = None
    status: Optional[FindingStatus] = None

    @property
    def evidence_uri(self) -> Optional[str]:
        """The single URI that goes on `Finding.evidence_uri`.

        The text, because that is what a reviewer reads and what a notice
        quotes. Everything else is reachable from `metadata.json` beside it.
        """
        return self.text_uri

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "url": self.url,
            "captured_at": self.captured_at.isoformat(),
            "text_uri": self.text_uri,
            "text_sha256": self.text_sha256,
            "text_chars": self.text_chars,
            "screenshot_uri": self.screenshot_uri,
            "screenshot_sha256": self.screenshot_sha256,
            "metadata_uri": self.metadata_uri,
            "withheld": self.withheld,
            "withheld_reason": self.withheld_reason,
            "status": self.status.value if self.status else None,
        }


@dataclass
class SnapshotCapture:
    store: EvidenceWriter
    screenshot: Optional[Screenshot] = None
    audit: Optional[Any] = None          # HarnessDeps.audit, if the caller has one
    _clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(timezone.utc), repr=False)

    # ------------------------------------------------------------------

    def capture(self, finding_id: str, page: PageSnapshot, *,
                unlawful: bool = False,
                classification: Optional[str] = None,
                extra: Optional[dict[str, Any]] = None) -> SnapshotRef:
        """Freeze one page. Never raises past a failed write.

        `unlawful=True` withholds everything: DESIGN Part III §4. The finding
        still records the address, a hash of the text, the classification and
        the time, so the escalation is actionable without our holding a copy.
        """
        now = self._clock()
        text = page.text or ""
        digest = _sha256(text.encode("utf-8"))

        if unlawful:
            ref = SnapshotRef(
                finding_id=finding_id, url=page.url, captured_at=now,
                text_sha256=digest, text_chars=len(text), withheld=True,
                withheld_reason=classification or "unlawful material",
                status=FindingStatus.ESCALATED_UNLAWFUL)
            log.warning("%s: withholding evidence for %s (%s) — recording the "
                        "address, a hash and the classification only",
                        AGENT_NAME, page.url, ref.withheld_reason)
            self._record(ref)
            return ref

        text_uri = self._put(finding_id, TEXT_NAME, text.encode("utf-8"),
                             "text/plain; charset=utf-8")

        shot_uri = shot_digest = None
        if self.screenshot is not None:
            image = self._shoot(page.url)
            if image:
                shot_digest = _sha256(image)
                shot_uri = self._put(finding_id, SCREENSHOT_NAME, image,
                                     "image/png")

        metadata = {
            "finding_id": finding_id,
            "url": page.url,
            "captured_at": now.isoformat(),
            "text": {"name": TEXT_NAME, "sha256": digest, "chars": len(text),
                     "uri": text_uri},
            "screenshot": ({"name": SCREENSHOT_NAME, "sha256": shot_digest,
                            "uri": shot_uri} if shot_uri else None),
            "media_refs": list(page.media_refs),
            "fetched_at": (page.fetched_at.isoformat()
                           if page.fetched_at else None),
            "from_cache": page.from_cache,
            "cache_age_seconds": page.cache_age_seconds,
            "note": ("Captured at discovery and never modified. This is "
                     "evidence, not a cache entry: it has no TTL and the "
                     "store exposes no delete."),
        }
        if extra:
            metadata["context"] = extra
        metadata_uri = self._put(
            finding_id, METADATA_NAME,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json")

        ref = SnapshotRef(
            finding_id=finding_id, url=page.url, captured_at=now,
            text_uri=text_uri, text_sha256=digest, text_chars=len(text),
            screenshot_uri=shot_uri, screenshot_sha256=shot_digest,
            metadata_uri=metadata_uri)
        log.info("%s finding=%s url=%s chars=%d screenshot=%s sha256=%s",
                 AGENT_NAME, finding_id, page.url, len(text),
                 bool(shot_uri), digest[:12])
        self._record(ref)
        return ref

    # ------------------------------------------------------------------

    def _put(self, finding_id: str, name: str, data: bytes,
             content_type: str) -> Optional[str]:
        try:
            return self.store.put_snapshot(finding_id, name, data, content_type)
        except Exception as exc:  # noqa: BLE001 - a failed write must not lose the finding
            log.warning("%s: could not store %s for %s: %s",
                        AGENT_NAME, name, finding_id, exc)
            return None

    def _shoot(self, url: str) -> Optional[bytes]:
        try:
            return self.screenshot(url) if self.screenshot else None
        except Exception as exc:  # noqa: BLE001 - text is the evidence that matters
            log.warning("%s: screenshot failed for %s: %s", AGENT_NAME, url, exc)
            return None

    def _record(self, ref: SnapshotRef) -> None:
        if self.audit is None:
            return
        event = {"ts": self._clock().isoformat(), "actor": AGENT_NAME,
                 "event": "snapshot"}
        event.update(ref.as_dict())
        self.audit.append(event)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Stopgap store — delete when WU-15's `consentinel/evidence/store.py` lands
# --------------------------------------------------------------------------

@dataclass
class LocalSnapshotStore:
    """Filesystem `EvidenceStore`, enough to demo and to test against.

    Deliberately in *this* file rather than `consentinel/evidence/`, so it
    cannot collide with Prachit's implementation of the same interface. It
    honours the two properties that matter and would matter in GCS too:

    * **no delete method**, here or on the interface;
    * **writes refuse to overwrite.** An evidence object that can be replaced
      is not evidence. A second capture of the same finding gets a new
      timestamped directory instead.
    """

    root: Path = field(default_factory=lambda: Path("./evidence"))

    def put_snapshot(self, finding_id: str, name: str, data: bytes,
                     content_type: str) -> str:
        directory = self.root / finding_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        if path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            directory = self.root / finding_id / stamp
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / name
        path.write_bytes(data)
        (directory / f"{name}.sha256").write_text(_sha256(data),
                                                  encoding="utf-8")
        return path.resolve().as_uri()

    def read(self, uri: str) -> bytes:
        """Read back for tests and the case-file view. Not part of the ABC."""
        from urllib.parse import unquote, urlparse

        return Path(unquote(urlparse(uri).path.lstrip("/"))).read_bytes()
