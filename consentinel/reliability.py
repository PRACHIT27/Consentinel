"""WU-14 — reliability for external calls, and one place that explains it.

`DESIGN.md` §0 and §2. The ticket asks for `consentinel/reliability.py`
wrapping every external call. **The classification, the backoff and the circuit
breaker already exist** — WU-00 built them inside the harness, and every agent
and tool in the project already runs through it. So this module does not
reimplement them: it re-exports the one implementation, adds the two pieces the
harness does not cover, and documents the policy in the place the ticket says
to look.

A second retry loop would be actively harmful. Two loops of three attempts is
nine requests, which turns one rate limit into an outage — the reason
`parallel_search` builds its SDK client with `max_retries=0`.

What lives here:

* `classify`, `backoff_seconds`, `CircuitBreaker`, `CircuitOpen`, `ErrorClass`
  — re-exported from `harness.errors`, so there is one definition of
  "transient" in the codebase.
* `POLICY` — the table from DESIGN §2 as data, so the UI and the docs can read
  the same numbers the code uses.
* `call()` — the harness for things that are not agents: a store write, a GCS
  upload, a Web Risk lookup. Same classification, same backoff, same breaker,
  no schema and no cache.
* `record_tool_call()` — the `audit_log.tool_calls` row WU-14 asks for:
  attempt number, latency, token usage.
* `SweepGuard` — the piece the harness genuinely lacks. The harness makes one
  *call* fail safely; this makes one *sweep* fail visibly, so a sweep that
  could not look is distinguishable from a sweep that looked and found nothing.

**The governing rule, restated because everything here serves it:** every
failure resolves toward doubt, never toward permission. `ambiguous`,
`unverified`, `degraded` — never `authorized`, never `cleared`. A compliance
tool that fails quietly is worse than no tool, because somebody signs off on
its output.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar

from consentinel.harness.errors import (  # noqa: F401 - re-exported on purpose
    BudgetExceeded,
    CapabilityError,
    CircuitBreaker,
    CircuitOpen,
    ErrorClass,
    HarnessError,
    ValidationError,
    backoff_seconds,
    classify,
)
from consentinel.harness.ports import HarnessDeps

T = TypeVar("T")

log = logging.getLogger("consentinel.reliability")

MAX_ATTEMPTS = 3
"""Transient failures only. Three attempts, not thirty: past three, the thing
on the other end is not having a bad second, it is down."""

BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 30.0
BREAKER_THRESHOLD = 5
BREAKER_RESET_S = 60.0
MAX_REPAIRS = 1

POLICY: dict[str, dict[str, Any]] = {
    ErrorClass.TRANSIENT.value: {
        "examples": [408, 429, 500, 502, 503, 504, "timeout", "connection reset"],
        "action": "exponential backoff with full jitter",
        "attempts": MAX_ATTEMPTS,
        "cap_s": BACKOFF_CAP_S,
    },
    ErrorClass.PERMANENT.value: {
        "examples": [400, 401, 403, 404, 405, 409, 422],
        "action": "do not retry; record and move on",
        "attempts": 1,
        "cap_s": 0,
    },
    ErrorClass.SEMANTIC.value: {
        "examples": ["schema violation", "quote not verbatim", "name mismatch"],
        "action": "one repair attempt, then ambiguous",
        "attempts": 1,
        "repairs": MAX_REPAIRS,
    },
}
"""DESIGN §2's table as data. Retrying a 401 thirty times is how a demo slot
gets burned; retrying a schema violation is how a parse failure becomes a
verdict."""


def default_breaker() -> CircuitBreaker:
    """One breaker per provider family, with the project's numbers."""
    return CircuitBreaker(threshold=BREAKER_THRESHOLD,
                          reset_after_s=BREAKER_RESET_S)


# --------------------------------------------------------------------------
# The harness for things that are not agents
# --------------------------------------------------------------------------

def call(fn: Callable[[], T], *, provider: str,
         deps: Optional[HarnessDeps] = None,
         breaker: Optional[CircuitBreaker] = None,
         max_attempts: int = MAX_ATTEMPTS,
         sleep: Callable[[float], None] = time.sleep,
         subject_id: Optional[str] = None,
         tokens: Optional[int] = None) -> T:
    """Run one external call under the project's retry policy.

    For calls with no schema and no cache — a store write, a bucket upload, a
    Web Risk lookup — where the full harness would be ceremony. Agents and
    model calls go through `Harness.run` instead; this is not a second path for
    them.

    Raises the last error rather than swallowing it. The *caller* decides which
    flavour of doubt the failure becomes, because only the caller knows whether
    it is looking at a finding (`ambiguous`), an asset (`unverified`) or a sweep
    (`degraded`).
    """
    deps = deps or HarnessDeps()
    breaker = breaker or default_breaker()
    started = time.monotonic()
    attempts = 0
    last: Optional[BaseException] = None

    breaker.guard(provider)          # refuse before spending anything

    while attempts < max(1, max_attempts):
        attempts += 1
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - classified immediately
            last = exc
            kind = classify(exc)
            if kind is ErrorClass.TRANSIENT:
                breaker.record_failure(provider)
                if attempts < max_attempts:
                    delay = backoff_seconds(attempts, base=BACKOFF_BASE_S,
                                            cap=BACKOFF_CAP_S)
                    log.info("%s: transient %s, attempt %d of %d, waiting %.2fs",
                             provider, type(exc).__name__, attempts,
                             max_attempts, delay)
                    sleep(delay)
                    continue
            elif not isinstance(exc, (CircuitOpen, BudgetExceeded, CapabilityError)):
                breaker.record_failure(provider)

            record_tool_call(deps, provider=provider, attempts=attempts,
                             latency_s=time.monotonic() - started, ok=False,
                             error_class=kind.value, reason=str(exc),
                             subject_id=subject_id, tokens=tokens)
            raise
        else:
            breaker.record_success(provider)
            record_tool_call(deps, provider=provider, attempts=attempts,
                             latency_s=time.monotonic() - started, ok=True,
                             subject_id=subject_id, tokens=tokens)
            return value

    raise last if last else RuntimeError(f"{provider}: no attempt was made")


def record_tool_call(deps: HarnessDeps, *, provider: str, attempts: int,
                     latency_s: float, ok: bool,
                     error_class: Optional[str] = None,
                     reason: Optional[str] = None,
                     subject_id: Optional[str] = None,
                     tokens: Optional[int] = None,
                     from_cache: bool = False,
                     cache_age_s: Optional[float] = None) -> None:
    """The `audit_log.tool_calls` row WU-14 asks for.

    Attempt number, latency and token usage, because "it was slow" and "it was
    retried twice and then worked" look identical in a log that records only
    the outcome.
    """
    deps.audit.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": provider,
        "event": "tool_call",
        "subject_id": subject_id,
        "ok": ok,
        "error_class": error_class,
        "reason": reason,
        "tool_calls": [{
            "provider": provider,
            "attempts": attempts,
            "retry_count": max(0, attempts - 1),
            "latency_s": round(latency_s, 4),
            "tokens": tokens,
            "from_cache": from_cache,
            "cache_age_s": cache_age_s,
        }],
    })


# --------------------------------------------------------------------------
# One sweep, failing visibly
# --------------------------------------------------------------------------

@dataclass
class SweepGuard:
    """Turns repeated provider failure into a *visible* degraded sweep.

    The harness protects one call. Nothing protected the sweep: twenty batches
    could each fail safely and produce an empty result set that renders exactly
    like "nothing out there". That is the failure mode DESIGN §0 is about — the
    quiet one that gets signed off.

    So: count consecutive failures per provider, and once the breaker opens,
    stop issuing calls and say why. `should_abort` is checked by the caller
    before each unit of work; `reason` is what the UI shows instead of a clean
    empty list.
    """

    breaker: CircuitBreaker = field(default_factory=default_breaker)
    provider: str = "parallel"
    failures: int = 0
    successes: int = 0
    aborted_at: Optional[int] = None

    def record(self, ok: bool) -> None:
        if ok:
            self.successes += 1
            self.breaker.record_success(self.provider)
        else:
            self.failures += 1
            self.breaker.record_failure(self.provider)
            if self.circuit_open and self.aborted_at is None:
                self.aborted_at = self.failures

    @property
    def circuit_open(self) -> bool:
        return self.breaker.is_open(self.provider)

    @property
    def should_abort(self) -> bool:
        """Stop spending on a provider that has stopped answering."""
        return self.circuit_open

    @property
    def degraded(self) -> bool:
        return bool(self.failures)

    @property
    def reason(self) -> Optional[str]:
        """The sentence a human reads instead of an empty result list."""
        if not self.failures:
            return None
        if self.circuit_open:
            return (f"{self.provider} failed {self.failures} times in a row; "
                    f"the sweep was abandoned after "
                    f"{self.successes + self.failures} of its batches. This is "
                    "not an empty result — we could not look.")
        return (f"{self.provider} failed {self.failures} of "
                f"{self.successes + self.failures} batches; the sweep is "
                "partial, not clean.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "batches_ok": self.successes,
            "batches_failed": self.failures,
            "circuit_open": self.circuit_open,
            "aborted_after": self.aborted_at,
            "degraded": self.degraded,
            "reason": self.reason,
        }
