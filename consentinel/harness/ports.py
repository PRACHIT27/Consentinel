"""Ports the harness depends on, with no-op defaults.

The harness needs cache (WU-19), audit (WU-18), Model Armor (WU-29), tracing and
metrics (WU-30) — none of which exist yet. It therefore depends on these
protocols rather than implementations, so it is testable today and those work
units plug in later without touching the runner.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, Protocol, runtime_checkable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CacheHit:
    value: Any
    fetched_at: datetime

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        return ((now or _utcnow()) - self.fetched_at).total_seconds()


@runtime_checkable
class CachePort(Protocol):
    def get(self, key: str) -> Optional[CacheHit]: ...
    def put(self, key: str, value: Any, ttl_seconds: Optional[int]) -> None: ...


@runtime_checkable
class AuditPort(Protocol):
    def append(self, event: dict[str, Any]) -> None: ...


@dataclass
class ArmorVerdict:
    """Result of a Model Armor screen.

    `blocked` is only ever True on a path configured inspect-and-block — the
    dossier output. On the triage path we label and continue, because a page
    trying to manipulate us is frequently the very page that is infringing, and
    blocking would suppress the finding.
    """

    findings: tuple[str, ...] = ()
    blocked: bool = False

    @property
    def injection_suspected(self) -> bool:
        return any(f in ("prompt_injection", "jailbreak") for f in self.findings)


@runtime_checkable
class ArmorPort(Protocol):
    def sanitize_prompt(self, template: str, text: str) -> ArmorVerdict: ...
    def sanitize_response(self, template: str, text: str) -> ArmorVerdict: ...


@runtime_checkable
class TracerPort(Protocol):
    def span(self, name: str, attributes: dict[str, Any]) -> Any: ...


@runtime_checkable
class MetricsPort(Protocol):
    def counter(self, name: str, value: int = 1, **labels: Any) -> None: ...
    def histogram(self, name: str, value: float, **labels: Any) -> None: ...


# --------------------------------------------------------------------------
# No-op defaults — real implementations land in WU-18, WU-19, WU-29, WU-30
# --------------------------------------------------------------------------

class NullCache:
    def get(self, key: str) -> Optional[CacheHit]:
        return None

    def put(self, key: str, value: Any, ttl_seconds: Optional[int]) -> None:
        return None


@dataclass
class MemoryAudit:
    """Collects events in memory. Append-only by construction: there is no
    update and no delete, mirroring the Store interface."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class NullArmor:
    def sanitize_prompt(self, template: str, text: str) -> ArmorVerdict:
        return ArmorVerdict()

    def sanitize_response(self, template: str, text: str) -> ArmorVerdict:
        return ArmorVerdict()


class NullTracer:
    @contextmanager
    def span(self, name: str, attributes: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield attributes


@dataclass
class MemoryMetrics:
    counters: dict[tuple, int] = field(default_factory=dict)
    histograms: dict[tuple, list[float]] = field(default_factory=dict)

    @staticmethod
    def _key(name: str, labels: dict[str, Any]) -> tuple:
        return (name,) + tuple(sorted(labels.items()))

    def counter(self, name: str, value: int = 1, **labels: Any) -> None:
        k = self._key(name, labels)
        self.counters[k] = self.counters.get(k, 0) + value

    def histogram(self, name: str, value: float, **labels: Any) -> None:
        self.histograms.setdefault(self._key(name, labels), []).append(value)


@dataclass
class HarnessDeps:
    """Everything the harness talks to. Defaults are no-ops so the harness runs
    standalone in tests and in early development."""

    cache: CachePort = field(default_factory=NullCache)
    audit: AuditPort = field(default_factory=MemoryAudit)
    armor: ArmorPort = field(default_factory=NullArmor)
    tracer: TracerPort = field(default_factory=NullTracer)
    metrics: MetricsPort = field(default_factory=MemoryMetrics)
    prompt_version: str = "v1"
