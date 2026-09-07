"""What every harnessed call returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from consentinel.harness.policy import FailState


@dataclass(frozen=True)
class HarnessResult:
    """The outcome of one agent invocation.

    The harness does not raise past its boundary for operational failures. It
    returns a result whose `fail_state` expresses doubt, because the calling
    pipeline must be able to record *why* a finding could not be judged rather
    than losing the candidate to an exception.

    A failed result never carries a value, and no code path produces
    `authorized` or `cleared` from here — that decision belongs to the
    reconciler, working from a successful extraction.
    """

    ok: bool
    value: Any = None
    fail_state: Optional[FailState] = None
    reason: Optional[str] = None

    # provenance, mirrored into the audit record
    agent: str = ""
    prompt_version: str = ""
    from_cache: bool = False
    cache_age_s: Optional[float] = None
    attempts: int = 0
    repairs: int = 0
    duration_s: float = 0.0
    armor_findings: tuple[str, ...] = ()
    injection_suspected: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def unwrap(self) -> Any:
        if not self.ok:
            raise RuntimeError(
                f"{self.agent}: no value — resolved to {self.fail_state} ({self.reason})"
            )
        return self.value
