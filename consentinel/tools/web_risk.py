"""`web_risk_check` — ask Google whether an address is already known to be bad.

Contract v2.0.0 (`DESIGN.md` Part III §1). Called **before** `fetch_page`,
never after. Our sweeps deliberately look at the corners of the web where
cloned voices are sold; Google already keeps the malware and phishing lists, so
there is no reason for us to be the one who finds out.

**Fails closed.** If the check itself errors — quota, network, API not enabled —
the address is reported unsafe and the page is skipped. One missed listing costs
nothing; opening a malware page costs more. That is the opposite of the
fail-open default most safety checks drift into, so it is asserted in tests
rather than left to good intentions.

`safe=False` does not mean the use was authorised or unauthorised. It means we
refused to look, the finding gets `status = BLOCKED_UNSAFE`, and its verdict
stays `ambiguous` — refusing to look is not deciding.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from consentinel.demo_mode import is_enabled as _demo_mode_enabled
from consentinel.demo_mode import miss as _demo_miss
from consentinel.harness import FailState, Harness, HarnessDeps, HarnessPolicy
from consentinel.tools.contracts import UrlRisk

TOOL_NAME = "web_risk_check"

THREAT_TYPE_NAMES = ("MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE",
                     "SOCIAL_ENGINEERING_EXTENDED_COVERAGE")
"""All four the API offers. Nothing is gained by asking about fewer."""

CHECK_FAILED = "CHECK_FAILED"
"""Reported in `threats` when the check itself did not complete. Distinguishes
"Google says this is malware" from "we could not ask" — both refuse the fetch,
but only the first is a statement about the page."""

CACHE_TTL_S = 6 * 3600

log = logging.getLogger("consentinel.web_risk")


class RiskClient(Protocol):
    """The one Web Risk method we use."""

    def search_uris(self, *, uri: str, threat_types: Any) -> Any: ...


@dataclass
class WebRiskCheck:
    """One configured caller of the Web Risk API.

    Same shape as `ParallelSearch`: harness for retries and caching, an
    injectable client so tests need no credentials.
    """

    deps: HarnessDeps = field(default_factory=HarnessDeps)
    client: Optional[RiskClient] = None
    timeout_s: float = 10.0
    max_attempts: int = 1
    cache_ttl_s: int = CACHE_TTL_S
    demo_mode: Optional[bool] = None   # None -> read DEMO_MODE at call time
    sleep: Callable[[float], None] = time.sleep
    _harness: Optional[Harness] = field(default=None, repr=False, compare=False)

    @property
    def policy(self) -> HarnessPolicy:
        return HarnessPolicy(
            agent_name=TOOL_NAME,
            # An unreadable page makes a finding we cannot judge.
            fail_state=FailState.AMBIGUOUS,
            timeout_s=self.timeout_s,
            max_attempts=self.max_attempts,
            tools=(TOOL_NAME,),
            cache="ttl",
            cache_ttl_s=self.cache_ttl_s,
        )

    @property
    def harness(self) -> Harness:
        if self._harness is None:
            self._harness = Harness(self.policy, self.deps, sleep=self.sleep)
        return self._harness

    def _sdk(self) -> RiskClient:
        if self.client is None:
            # Lazy: constructing the client needs credentials, and tests must
            # not need any.
            from google.cloud import webrisk_v1

            self.client = webrisk_v1.WebRiskServiceClient()
        return self.client

    # ------------------------------------------------------------------

    def check(self, url: str, *, subject_id: Optional[str] = None) -> UrlRisk:
        """Never raises. An unanswerable question resolves to *unsafe*."""
        if not (url or "").strip():
            return self._refuse(url, "empty url", subject_id)

        def invoke(_hint: Optional[str]) -> tuple[bool, tuple[str, ...]]:
            if _demo_mode_enabled(self.demo_mode):
                # WU-20. Note where this lands: an unanswerable check is
                # unsafe, so a cold cache in demo mode skips the page rather
                # than opening it. Loud, and still not permission.
                raise _demo_miss(TOOL_NAME, url)

            from google.cloud import webrisk_v1  # noqa: PLC0415

            threat_types = [getattr(webrisk_v1.ThreatType, name)
                            for name in THREAT_TYPE_NAMES]
            response = self._sdk().search_uris(uri=url, threat_types=threat_types)
            threat = getattr(response, "threat", None)
            found = tuple(_threat_names(threat))
            return (not found, found)

        result = self.harness.run(invoke, cache_key=f"{TOOL_NAME}:{url}",
                                  subject_id=subject_id, provider="webrisk")
        if not result.ok:
            return self._refuse(url, result.reason or "check failed", subject_id,
                                from_cache=result.from_cache)

        safe, threats = result.value
        risk = UrlRisk(url=url, safe=safe, threats=list(threats),
                       checked_at=datetime.now(timezone.utc))
        self._record(risk, result.from_cache, result.cache_age_s, subject_id)
        return risk

    # ------------------------------------------------------------------

    def _refuse(self, url: str, reason: str, subject_id: Optional[str],
                from_cache: bool = False) -> UrlRisk:
        """Fail closed: unsafe, with `CHECK_FAILED` so the reason survives."""
        risk = UrlRisk(url=url, safe=False, threats=[CHECK_FAILED],
                       checked_at=datetime.now(timezone.utc))
        log.warning("%s url=%s safe=False threats=[CHECK_FAILED] reason=%r "
                    "(failing closed: not fetching)", TOOL_NAME, url, reason)
        self._record(risk, from_cache, None, subject_id, reason=reason)
        return risk

    def _record(self, risk: UrlRisk, from_cache: bool,
                cache_age_s: Optional[float], subject_id: Optional[str],
                reason: Optional[str] = None) -> None:
        self.deps.audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": TOOL_NAME,
            "event": "tool_call",
            "subject_id": subject_id,
            "ok": reason is None,
            "reason": reason,
            "api": "webrisk.searchUris",
            "url": risk.url,
            "safe": risk.safe,
            "threats": list(risk.threats),
            "from_cache": from_cache,
            "cache_age_s": cache_age_s,
        })
        if reason is None:
            log.info("%s url=%s safe=%s threats=%s cache=%s", TOOL_NAME,
                     risk.url, risk.safe, list(risk.threats),
                     "hit" if from_cache else "miss")


def _threat_names(threat: Any) -> list[str]:
    """Threat type enums -> plain names for `reasoning` and the screen."""
    if threat is None:
        return []
    names: list[str] = []
    for value in getattr(threat, "threat_types", None) or []:
        names.append(getattr(value, "name", None) or str(value))
    return names


# --------------------------------------------------------------------------
# The frozen contract entry point
# --------------------------------------------------------------------------

_default: Optional[WebRiskCheck] = None


def configure(**kwargs: Any) -> WebRiskCheck:
    global _default
    _default = WebRiskCheck(**kwargs)
    return _default


def default_checker() -> WebRiskCheck:
    global _default
    if _default is None:
        _default = WebRiskCheck()
    return _default


def is_enabled() -> bool:
    """`WEB_RISK_ENABLED=false` skips the check entirely.

    For local development without the API enabled. It is **not** a fail-open
    path: with the check disabled the caller is told the address was never
    checked, and `fetch_page` logs that it went ahead unchecked. Default is on.
    """
    return os.environ.get("WEB_RISK_ENABLED", "true").strip().lower() not in (
        "0", "false", "no")


def web_risk_check(url: str) -> UrlRisk:
    """Ask Google whether this address is already known to be dangerous.

    The frozen signature from `tools/contracts.py`. Returns `safe=False` on any
    failure of the check itself — see this module's docstring.
    """
    return default_checker().check(url)
