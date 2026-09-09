"""Metrics, emitted as log lines rather than time series.

The design lists fourteen metrics. Two of them are the reason this file exists:

    extraction.validation_failures{reason}   the guardrail's own telemetry.
                                             A non-zero rate here is the model
                                             trying to fabricate a citation and
                                             being caught.
    model_armor.detections{type}             injection attempts, PII, bad links.

Together they let a dashboard *demonstrate* the security posture instead of
asserting it, which is worth more than any sentence in a writeup.

**Why log lines and not Cloud Monitoring time series.** Writing custom time
series means a client, a metric descriptor per metric, resource labels, and a
write quota that rejects more than one point per series per interval — which a
sweep hitting the same counter repeatedly will trip. Log-based metrics are a
first-class Cloud Monitoring feature: emit a structured line, define a counter or
distribution over it once, and the aggregation is done for you. For fourteen
metrics and one day of runway that is the right trade, and it degrades to
"grep the logs" if the dashboard never gets built.

Each line carries `metric`, `value`, `kind` and its labels, so a log-based metric
is a filter on `jsonPayload.metric`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from consentinel.obs.logs import JsonLogger

# The names the design settled on. Kept here so a typo in an agent shows up as
# an unknown-metric warning rather than a silently missing line on a dashboard.
KNOWN = frozenset({
    # operational
    "sweep.duration_seconds", "sweep.candidates_discovered", "sweep.candidates_triaged",
    "tool.latency_seconds", "tool.errors", "circuit_breaker.opened",
    "cache.hit_ratio", "cache.hits", "cache.misses", "tokens.used",
    "agent.duration_seconds", "agent.runs",
    # quality and security — the two that make a dashboard worth showing
    "verdicts.total", "verdicts.ambiguous_ratio",
    "extraction.validation_failures", "model_armor.detections",
    "evidence.snapshots_written", "assets.clearance_state",
})


class Metrics:
    """Satisfies the harness `MetricsPort`.

    Also keeps a running tally in memory, which is what the tests assert against
    and what a local run can print without a dashboard existing.
    """

    def __init__(self, logger: Optional[JsonLogger] = None, *, warn_unknown: bool = True) -> None:
        self.log = logger or JsonLogger()
        self.warn_unknown = warn_unknown
        self.counters: dict[tuple, int] = defaultdict(int)
        self.observations: dict[tuple, list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, labels: dict[str, Any]) -> tuple:
        return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))

    def _check(self, name: str) -> None:
        if self.warn_unknown and name not in KNOWN:
            self.log.warning(
                "unknown metric name; it will not appear on the dashboard",
                metric_name=name,
            )

    def counter(self, name: str, value: int = 1, **labels: Any) -> None:
        self._check(name)
        self.counters[self._key(name, labels)] += value
        self.log.info("metric", metric=name, kind="counter", value=value, **labels)

    def histogram(self, name: str, value: float, **labels: Any) -> None:
        self._check(name)
        self.observations[self._key(name, labels)].append(value)
        self.log.info("metric", metric=name, kind="distribution",
                      value=round(value, 4), **labels)

    # ------------------------------------------------------------------ reading

    def total(self, name: str, **labels: Any) -> int:
        if labels:
            return self.counters.get(self._key(name, labels), 0)
        return sum(v for (n, _), v in self.counters.items() if n == name)

    def values(self, name: str, **labels: Any) -> list[float]:
        if labels:
            return list(self.observations.get(self._key(name, labels), []))
        return [v for (n, _), vs in self.observations.items() if n == name for v in vs]

    def snapshot(self) -> dict[str, Any]:
        """Everything gathered so far, for a local run or a test."""
        out: dict[str, Any] = {}
        for (name, labels), v in sorted(self.counters.items()):
            out[_label(name, labels)] = v
        for (name, labels), vs in sorted(self.observations.items()):
            if vs:
                out[_label(name, labels)] = {
                    "count": len(vs),
                    "mean": round(sum(vs) / len(vs), 4),
                    "max": round(max(vs), 4),
                }
        return out


def _label(name: str, labels: tuple) -> str:
    if not labels:
        return name
    inner = ",".join(f"{k}={v}" for k, v in labels)
    return f"{name}{{{inner}}}"
