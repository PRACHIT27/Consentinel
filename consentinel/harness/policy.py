"""Per-agent policy. The declaration an agent makes about what it may do."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class FailState(str, Enum):
    """Where an agent lands when it cannot succeed.

    Every value here expresses doubt. There is deliberately no success value:
    a failure must never resolve to `authorized` or `cleared`. See DESIGN.md
    Part I section 0.
    """

    AMBIGUOUS = "ambiguous"      # a finding we could not judge
    UNVERIFIED = "unverified"    # an asset whose coverage we could not establish
    DEGRADED = "degraded"        # a sweep that could not complete


CacheRegime = Literal["content", "ttl", "none"]


@dataclass(frozen=True)
class HarnessPolicy:
    """What an agent is allowed to do, and how it behaves when things go wrong.

    `tools` is not documentation. It is the capability boundary: the harness
    refuses any tool call whose name is absent from this tuple, so an agent
    cannot reach a capability it did not declare. Triage and MediaTriage
    declare `tools=()` because they read attacker-controlled content, and a
    component with no capability cannot be made to act.
    """

    agent_name: str
    fail_state: FailState

    # reliability
    timeout_s: float = 60.0
    max_attempts: int = 3               # transient retries, not counting the first try
    max_repairs: int = 1                # bounded: a parse failure must never become a verdict

    # capability boundary
    tools: tuple[str, ...] = ()

    # model
    output_schema: Optional[type] = None   # None for agents that make no model call
    temperature: float = 0.0               # only the dossier draft may raise this

    # cache
    cache: CacheRegime = "none"
    cache_ttl_s: Optional[int] = None

    # Model Armor templates, per trust context. None means the path is not screened.
    armor_prompt: Optional[str] = None     # inspect-only on untrusted input
    armor_response: Optional[str] = None   # inspect-and-block on outward-facing text

    labels: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cache == "ttl" and self.cache_ttl_s is None:
            raise ValueError(f"{self.agent_name}: cache='ttl' requires cache_ttl_s")
        if self.cache == "content" and self.cache_ttl_s is not None:
            raise ValueError(
                f"{self.agent_name}: content-addressed entries never expire; "
                "drop cache_ttl_s (DESIGN.md Part II section 4.2)"
            )
        if self.max_repairs > 1:
            raise ValueError(
                f"{self.agent_name}: repair is bounded at one attempt. Beyond that the "
                "result resolves to fail_state rather than being retried into existence"
            )

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.tools
