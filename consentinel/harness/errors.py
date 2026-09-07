"""Error classification, backoff and the circuit breaker.

Never blanket-retry. Retrying a 401 thirty times is how a demo slot gets burned.
See DESIGN.md Part I section 2.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ErrorClass(str, Enum):
    TRANSIENT = "transient"    # retry with backoff
    PERMANENT = "permanent"    # do not retry; record and move on
    SEMANTIC = "semantic"      # one repair attempt, then give up


class HarnessError(Exception):
    """Base for errors the harness understands."""


class CapabilityError(HarnessError):
    """An agent attempted a tool absent from its HarnessPolicy.tools.

    This is not a recoverable condition and is never retried. It means an agent
    tried to exceed the capability boundary, which is a bug in the agent or an
    attempt to make it act — either way the call does not happen.
    """


class ValidationError(HarnessError):
    """Model output failed its schema or a field validator. Semantic: repairable once."""


class BudgetExceeded(HarnessError):
    """A timeout, wall-clock or token ceiling was hit. Permanent for this call."""


class CircuitOpen(HarnessError):
    """A provider's breaker is open. The sweep is degraded, not empty."""


_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_PERMANENT_STATUS = frozenset({400, 401, 403, 404, 405, 409, 422})

_TRANSIENT_MARKERS = (
    "timeout", "timed out", "connection reset", "connection aborted",
    "temporarily unavailable", "deadline exceeded", "unavailable",
)


def classify(exc: BaseException) -> ErrorClass:
    """Decide how to treat a failure. Errors we do not recognise are PERMANENT.

    Defaulting to PERMANENT is deliberate: an unrecognised error retried three
    times is three times the cost and no more information.
    """
    if isinstance(exc, (ValidationError,)):
        return ErrorClass.SEMANTIC
    if isinstance(exc, (CapabilityError, BudgetExceeded, CircuitOpen)):
        return ErrorClass.PERMANENT
    if isinstance(exc, TimeoutError):
        return ErrorClass.TRANSIENT

    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        if status in _TRANSIENT_STATUS:
            return ErrorClass.TRANSIENT
        if status in _PERMANENT_STATUS:
            return ErrorClass.PERMANENT

    text = str(exc).lower()
    if any(m in text for m in _TRANSIENT_MARKERS):
        return ErrorClass.TRANSIENT
    return ErrorClass.PERMANENT


def backoff_seconds(attempt: int, *, base: float = 0.5, cap: float = 30.0,
                    rng: Optional[random.Random] = None) -> float:
    """Exponential backoff with full jitter.

    Full jitter rather than fixed backoff: several agents retrying in lockstep
    is how a rate limit becomes a thundering herd.
    """
    r = rng or random
    return r.uniform(0.0, min(cap, base * (2 ** max(0, attempt))))


@dataclass
class CircuitBreaker:
    """Opens after N consecutive failures from one provider.

    The point is not to save calls. It is that a sweep which could not look must
    be distinguishable from a sweep that looked and found nothing — otherwise a
    human signs off on an empty result that means the opposite of what it says.
    """

    threshold: int = 5
    reset_after_s: float = 60.0
    _failures: dict[str, int] = field(default_factory=dict)
    _opened_at: dict[str, float] = field(default_factory=dict)

    def _now(self) -> float:
        return time.monotonic()

    def is_open(self, provider: str) -> bool:
        opened = self._opened_at.get(provider)
        if opened is None:
            return False
        if self._now() - opened >= self.reset_after_s:
            self._failures.pop(provider, None)
            self._opened_at.pop(provider, None)
            return False
        return True

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)
        self._opened_at.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        n = self._failures.get(provider, 0) + 1
        self._failures[provider] = n
        if n >= self.threshold:
            self._opened_at.setdefault(provider, self._now())

    def guard(self, provider: str) -> None:
        if self.is_open(provider):
            raise CircuitOpen(
                f"{provider}: circuit open after {self._failures.get(provider)} "
                "consecutive failures; sweep is degraded, not empty"
            )
