"""WU-05 — `parallel_search`, the judged partner integration.

Implements the frozen signature in `tools/contracts.py` on top of the official
`parallel-web` SDK. The competition rules require the integration to be *called
at runtime*, not merely named in the README, so every call writes one readable
log line and one audit row: that is the submission evidence.

Verified against the installed SDK (`parallel-web` 1.3.3) rather than from
memory, because the shape differs from the docs summary in CLAUDE.md in two
places worth knowing:

* the method is `client.search(...)` — top level, not `client.beta.search`;
* `location`, `max_results` and `source_policy` are **not** top-level
  arguments. They live inside `advanced_settings`. Only `search_queries`,
  `objective`, `mode`, `max_chars_total`, `session_id` and `client_model` are.

Everything else in WU-04's answer held: 2-3 keyword queries of 3-6 words each,
`location` is a lowercase ISO 3166-1 alpha-2 code, and multilingual queries
need no configuration beyond writing them in the target language.

Reliability, retries, circuit breaking, caching and fail-safe come from the
WU-00 harness rather than being re-implemented here — so the SDK client is
constructed with `max_retries=0`. Two independent retry loops multiply cost and
turn one rate limit into nine.

`excerpts` are LLM-selected and truncated. Fine to triage on, never evidence:
evidence is our own immutable snapshot (WU-15).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol, Sequence

from consentinel.harness import FailState, Harness, HarnessDeps, HarnessPolicy
from consentinel.harness.result import HarnessResult
from consentinel.demo_mode import DemoModeMiss as _DemoModeMiss
from consentinel.demo_mode import is_enabled as _demo_mode_enabled
from consentinel.demo_mode import miss as _demo_miss
from consentinel.store.base import Locale
from consentinel.tools.contracts import SearchResponse, SearchResult

TOOL_NAME = "parallel_search"

DEFAULT_MODE = "basic"
"""A sweep is many narrow queries, so advanced-mode reranking is cost we do not
need. The SDK defaults to `advanced` when omitted — we always send a mode."""

CACHE_TTL_S = 6 * 3600
"""Six hours, the low end of the 6-24h band in WU-05. A sweep re-run inside one
working session should not pay twice; a sweep re-run tomorrow should look
again."""

DEGRADED_WARNING_TYPE = "consentinel_degraded"
"""Marks a response that failed rather than one that found nothing. Rule 10:
those two must never look alike, or a human signs off on an empty result that
means the opposite of what it says. Callers check `is_degraded`."""

# 2-3 queries of 3-6 words each is what the API is tuned for. Out-of-band
# queries are logged, not rejected: a clumsy plan should cost result quality,
# not degrade the whole sweep.
PREFERRED_QUERY_COUNT = (2, 3)
PREFERRED_QUERY_WORDS = (3, 6)

log = logging.getLogger("consentinel.parallel_search")
"""The call log. Submission evidence — it gets screenshotted, so keep it to one
readable line per call."""


DemoModeMiss = _DemoModeMiss
"""Re-exported for callers that catch it by name. The definition lives in
`consentinel.demo_mode` so every client raises the same type — WU-20."""


class SearchClient(Protocol):
    """The one SDK method we use. Declared as a protocol so tests can pass a
    fake without a network or an API key."""

    def search(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class SourcePolicy:
    """`advanced_settings.source_policy` — domain and freshness filtering.

    Worth using per WU-05: excluding known-irrelevant hosts and setting
    `after_date` is cheaper than triaging the results they would produce.
    """

    exclude_domains: tuple[str, ...] = ()
    include_domains: tuple[str, ...] = ()
    after_date: Optional[str] = None  # YYYY-MM-DD

    def as_sdk(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.exclude_domains:
            out["exclude_domains"] = list(self.exclude_domains)
        if self.include_domains:
            out["include_domains"] = list(self.include_domains)
        if self.after_date:
            out["after_date"] = self.after_date
        return out


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------

@dataclass
class ParallelSearch:
    """One configured caller of Parallel's Search API.

    Holds the harness deps so WU-18 (audit) and WU-19 (cache) plug in by
    construction rather than by editing this file.
    """

    deps: HarnessDeps = field(default_factory=HarnessDeps)
    client: Optional[SearchClient] = None
    demo_mode: Optional[bool] = None   # None -> read DEMO_MODE at call time
    timeout_s: float = 30.0
    max_attempts: int = 2
    cache_ttl_s: int = CACHE_TTL_S
    sleep: Callable[[float], None] = time.sleep   # tests pass a no-op
    _harness: Optional[Harness] = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------

    @property
    def policy(self) -> HarnessPolicy:
        return HarnessPolicy(
            agent_name=TOOL_NAME,
            # A sweep that could not look is degraded, never empty.
            fail_state=FailState.DEGRADED,
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

    def _sdk(self) -> SearchClient:
        if self.client is None:
            self.client = _build_sdk_client()
        return self.client

    def _in_demo_mode(self) -> bool:
        return _demo_mode_enabled(self.demo_mode)

    # ------------------------------------------------------------------

    def search(
        self,
        objective: str,
        search_queries: Sequence[str],
        locale: Locale,
        max_results: int = 10,
        mode: str = DEFAULT_MODE,
        session_id: Optional[str] = None,
        *,
        source_policy: Optional[SourcePolicy] = None,
        max_chars_total: Optional[int] = None,
        subject_id: Optional[str] = None,
    ) -> SearchResponse:
        """Run one search batch. Never raises: failures come back degraded.

        Beyond the frozen signature: `source_policy`, `max_chars_total` and
        `subject_id` (the performer the sweep is for, for the audit trail).
        """
        result = self.search_detailed(
            objective, search_queries, locale, max_results, mode, session_id,
            source_policy=source_policy, max_chars_total=max_chars_total,
            subject_id=subject_id,
        )
        if result.ok:
            return result.value
        return _degraded_response(session_id, result.reason or "unknown error")

    def search_detailed(
        self,
        objective: str,
        search_queries: Sequence[str],
        locale: Locale,
        max_results: int = 10,
        mode: str = DEFAULT_MODE,
        session_id: Optional[str] = None,
        *,
        source_policy: Optional[SourcePolicy] = None,
        max_chars_total: Optional[int] = None,
        subject_id: Optional[str] = None,
    ) -> HarnessResult:
        """As `search`, but returns the harness result — attempts, cache age,
        fail state. TextSweep (WU-07) needs those to report a partial sweep."""
        queries = [q.strip() for q in search_queries if q and q.strip()]
        location = locale.region.lower()
        policy_fields = source_policy.as_sdk() if source_policy else {}

        cache_key = _cache_key(
            objective=objective, queries=queries, locale=locale, mode=mode,
            max_results=max_results, max_chars_total=max_chars_total,
            source_policy=policy_fields,
        )

        def invoke(_repair_hint: Optional[str]) -> SearchResponse:
            # Validated inside the harness, not before it: one bad batch out of
            # twenty should degrade that batch, not raise through the sweep.
            if not queries:
                raise ValueError(
                    f"{TOOL_NAME}: search_queries is empty; the API requires at "
                    "least one keyword query"
                )
            _warn_on_query_shape(queries)

            if self._in_demo_mode():
                raise _demo_miss(TOOL_NAME, cache_key)

            self.harness.guard_tool(TOOL_NAME)

            advanced: dict[str, Any] = {"location": location,
                                        "max_results": max_results}
            if policy_fields:
                advanced["source_policy"] = policy_fields

            kwargs: dict[str, Any] = {
                "search_queries": queries,
                "objective": objective,
                "mode": mode,
                "advanced_settings": advanced,
            }
            if session_id is not None:
                # Ties every call of one sweep together on Parallel's side.
                kwargs["session_id"] = session_id
            if max_chars_total is not None:
                kwargs["max_chars_total"] = max_chars_total

            raw = self._sdk().search(**kwargs)
            return _to_response(raw, max_results)

        result = self.harness.run(
            invoke, cache_key=cache_key, subject_id=subject_id, provider="parallel",
        )
        response = result.value if result.ok else None
        self._audit_call(result, queries, locale, location, mode, session_id,
                         subject_id, response)
        _log_call(result, queries, locale, location, mode, response)
        return result

    # ------------------------------------------------------------------

    def _audit_call(
        self, result: HarnessResult, queries: list[str], locale: Locale,
        location: str, mode: str, session_id: Optional[str],
        subject_id: Optional[str], response: Optional[SearchResponse],
    ) -> None:
        """The harness already wrote its reliability envelope. This row is the
        partner-integration record the submission needs: which queries went to
        the SDK, in which locale, and what came back."""
        self.deps.audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": TOOL_NAME,
            "event": "tool_call",
            "subject_id": subject_id,
            "ok": result.ok,
            "fail_state": result.fail_state.value if result.fail_state else None,
            "reason": result.reason,
            "sdk": "parallel-web",
            "api": "search",
            "queries": list(queries),
            "locale": str(locale),
            "location": location,
            "mode": mode,
            "session_id": (response.session_id if response else session_id),
            "search_id": response.search_id if response else None,
            "result_count": len(response.results) if response else 0,
            "from_cache": result.from_cache,
            "cache_age_s": result.cache_age_s,
            "attempts": result.attempts,
            "latency_s": round(result.duration_s, 4),
        })


# --------------------------------------------------------------------------
# Mapping and logging
# --------------------------------------------------------------------------

def _to_response(raw: Any, max_results: int) -> SearchResponse:
    """SDK `SearchResult` -> our `SearchResponse`.

    Attribute access rather than `model_dump`-and-index so a fake client in a
    test and the real Pydantic model take the same path.
    """
    results: list[SearchResult] = []
    for r in list(getattr(raw, "results", None) or [])[:max_results]:
        results.append(SearchResult(
            url=getattr(r, "url", "") or "",
            title=getattr(r, "title", None),
            publish_date=getattr(r, "publish_date", None),
            excerpts=list(getattr(r, "excerpts", None) or []),
            raw=_as_dict(r),
        ))
    return SearchResponse(
        search_id=getattr(raw, "search_id", "") or "",
        results=results,
        warnings=[_as_dict(w) for w in (getattr(raw, "warnings", None) or [])],
        session_id=getattr(raw, "session_id", None),
    )


def _as_dict(obj: Any) -> dict[str, Any]:
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - provenance is best-effort
                log.debug("%s: could not serialise %r: %s", TOOL_NAME, obj, exc)
    if isinstance(obj, dict):
        return dict(obj)
    return {"repr": repr(obj)}


def _degraded_response(session_id: Optional[str], reason: str) -> SearchResponse:
    """Rule 10 in shape: no results, and a warning saying we could not look."""
    return SearchResponse(
        search_id="",
        results=[],
        warnings=[{"type": DEGRADED_WARNING_TYPE, "message": reason}],
        session_id=session_id,
    )


def is_degraded(response: SearchResponse) -> bool:
    """True when the call failed. An empty `results` list alone does not mean
    this — a clean search can legitimately find nothing."""
    return any(w.get("type") == DEGRADED_WARNING_TYPE for w in response.warnings)


def degraded_reason(response: SearchResponse) -> Optional[str]:
    for w in response.warnings:
        if w.get("type") == DEGRADED_WARNING_TYPE:
            return w.get("message")
    return None


def _log_call(result: HarnessResult, queries: list[str], locale: Locale,
              location: str, mode: str, response: Optional[SearchResponse]) -> None:
    cache = "hit" if result.from_cache else "miss"
    parts = [
        f"{TOOL_NAME} sdk=parallel-web api=search",
        f"mode={mode}",
        f"locale={locale} location={location}",
        f"cache={cache}",
        f"results={len(response.results) if response else 0}",
        f"latency_ms={result.duration_s * 1000:.0f}",
        f"attempts={result.attempts}",
        f"search_id={response.search_id if response else '-'}",
        f"session_id={(response.session_id if response else None) or '-'}",
        f"queries={json.dumps(queries, ensure_ascii=False)}",
    ]
    if result.from_cache and result.cache_age_s is not None:
        parts.insert(4, f"cache_age_s={result.cache_age_s:.0f}")
    if result.ok:
        log.info(" ".join(parts))
    else:
        parts.append(f"status={result.fail_state.value if result.fail_state else 'error'}")
        parts.append(f"reason={result.reason!r}")
        log.warning(" ".join(parts))


def enable_call_log(level: int = logging.INFO) -> None:
    """Opt-in stderr handler for the call log.

    A library should not configure logging, but the demo and the runtime-evidence
    screenshot both need these lines visible from a plain script.
    """
    if not any(getattr(h, "_consentinel_call_log", False) for h in log.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler._consentinel_call_log = True  # type: ignore[attr-defined]
        log.addHandler(handler)
    log.setLevel(level)


# --------------------------------------------------------------------------
# Cache key
# --------------------------------------------------------------------------

def _cache_key(*, objective: str, queries: list[str], locale: Locale, mode: str,
               max_results: int, max_chars_total: Optional[int],
               source_policy: dict[str, Any]) -> str:
    """Keyed on queries + locale, per WU-05, plus everything else that changes
    the result set.

    `locale` is in the key because it changes what the web returns (hard rule
    8). `prompt_version` is not: this is an API call, not a model call, so no
    prompt participates. `session_id` is deliberately excluded — it groups one
    sweep's calls on Parallel's side and would otherwise fragment the cache
    across sweeps that ask identical questions.
    """
    payload = {
        "tool": TOOL_NAME,
        "objective": objective,
        "queries": sorted(queries),   # order does not change the result set
        "locale": str(locale).lower(),
        "mode": mode,
        "max_results": max_results,
        "max_chars_total": max_chars_total,
        "source_policy": source_policy,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"{TOOL_NAME}:{digest}"


def _warn_on_query_shape(queries: list[str]) -> None:
    lo_n, hi_n = PREFERRED_QUERY_COUNT
    if not lo_n <= len(queries) <= hi_n:
        log.warning(
            "%s: %d queries in this batch; the API is tuned for %d-%d",
            TOOL_NAME, len(queries), lo_n, hi_n,
        )
    lo_w, hi_w = PREFERRED_QUERY_WORDS
    for q in queries:
        words = len(q.split())
        if not lo_w <= words <= hi_w:
            log.warning(
                "%s: query %r is %d words; the API is tuned for %d-%d",
                TOOL_NAME, q, words, lo_w, hi_w,
            )


def _build_sdk_client() -> SearchClient:
    """The real SDK client. Imported lazily so tests and DEMO_MODE need neither
    the package nor an API key."""
    from parallel import Parallel  # noqa: PLC0415 - lazy on purpose

    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv()
    except ImportError:
        pass

    # max_retries=0: the harness owns retry, backoff and circuit breaking.
    return Parallel(max_retries=0)


# --------------------------------------------------------------------------
# The frozen contract entry point
# --------------------------------------------------------------------------

_default_tool: Optional[ParallelSearch] = None


def configure(**kwargs: Any) -> ParallelSearch:
    """Install the process-wide tool. Call once at startup to hand it the real
    cache and audit sink (WU-18, WU-19)."""
    global _default_tool
    _default_tool = ParallelSearch(**kwargs)
    return _default_tool


def default_tool() -> ParallelSearch:
    global _default_tool
    if _default_tool is None:
        _default_tool = ParallelSearch()
    return _default_tool


def parallel_search(
    objective: str,
    search_queries: list[str],
    locale: Locale,
    max_results: int = 10,
    mode: str = DEFAULT_MODE,
    session_id: Optional[str] = None,
) -> SearchResponse:
    """Search the open web via Parallel's Search API (official `parallel-web` SDK).

    The frozen signature from `tools/contracts.py`; see that docstring for the
    parameter meanings and this module's for what the SDK actually accepts.
    Callers wanting attempts, cache age or fail state should use
    `default_tool().search_detailed(...)`.
    """
    return default_tool().search(
        objective, search_queries, locale, max_results, mode, session_id,
    )
