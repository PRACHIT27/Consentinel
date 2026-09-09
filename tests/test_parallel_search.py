"""WU-05 acceptance.

Done when: a search returns SearchResponse and the call log shows the SDK being
invoked with queries, location and result count.

The SDK is faked. These tests assert the *mapping* — that our arguments land in
the shape `parallel-web` 1.3.3 actually accepts, including `location` and
`max_results` living inside `advanced_settings` rather than at the top level.
A live smoke call needs PARALLEL_API_KEY and is not a unit test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from consentinel.harness import FailState, HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.harness.ports import CacheHit
from consentinel.store.base import Locale
from consentinel.tools.contracts import SearchResponse
from consentinel.tools.parallel_search import (
    DEGRADED_WARNING_TYPE,
    ParallelSearch,
    SourcePolicy,
    degraded_reason,
    is_degraded,
)

PT_BR = Locale(language="pt", region="BR")
EN_US = Locale(language="en", region="US")

QUERIES = ["mira vance voz clonada", "anúncio voz sintética mira"]
OBJECTIVE = "Find pages selling or advertising an AI voice clone of Mira Vance."


# ---------------------------------------------------------------- fake SDK

@dataclass
class FakeResult:
    url: str
    title: Optional[str] = None
    publish_date: Optional[str] = None
    excerpts: list[str] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title,
                "publish_date": self.publish_date, "excerpts": list(self.excerpts)}


@dataclass
class FakeSearchResult:
    search_id: str = "search_abc123"
    session_id: str = "sweep-1"
    results: list[FakeResult] = field(default_factory=list)
    warnings: list[Any] = field(default_factory=list)


class FakeClient:
    """Records the kwargs each call was made with."""

    def __init__(self, response: Optional[FakeSearchResult] = None,
                 errors: Optional[list[BaseException]] = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or FakeSearchResult(results=[
            FakeResult("https://example.com/a", "A", "2026-08-01", ["snippet a"]),
            FakeResult("https://example.com/b", "B", None, ["snippet b1", "b2"]),
        ])
        self._errors = list(errors or [])

    def search(self, **kwargs: Any) -> FakeSearchResult:
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
        return self._response


class MemoryCache:
    """Stand-in for WU-19. TTL honoured so the expiry path is exercised."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[Any, datetime, Optional[int]]] = {}
        self.puts = 0

    def get(self, key: str) -> Optional[CacheHit]:
        found = self.entries.get(key)
        if found is None:
            return None
        value, fetched_at, ttl = found
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if ttl is not None and age > ttl:
            del self.entries[key]
            return None
        return CacheHit(value=value, fetched_at=fetched_at)

    def put(self, key: str, value: Any, ttl_seconds: Optional[int]) -> None:
        self.puts += 1
        self.entries[key] = (value, datetime.now(timezone.utc), ttl_seconds)

    def preload(self, key: str, value: Any, age_s: float = 0.0) -> None:
        self.entries[key] = (
            value, datetime.now(timezone.utc) - timedelta(seconds=age_s), None)


def tool(client: Optional[FakeClient] = None, **kw: Any
         ) -> tuple[ParallelSearch, FakeClient, MemoryCache, MemoryAudit]:
    client = client or FakeClient()
    cache = MemoryCache()
    audit = MemoryAudit()
    deps = HarnessDeps(cache=cache, audit=audit, metrics=MemoryMetrics())
    kw.setdefault("sleep", lambda _s: None)   # no real backoff in unit tests
    kw.setdefault("demo_mode", False)         # never read the ambient DEMO_MODE
    t = ParallelSearch(deps=deps, client=client, **kw)
    return t, client, cache, audit


# ---------------------------------------------------------------- happy path

def test_returns_search_response_mapped_from_the_sdk():
    t, client, _, _ = tool()

    r = t.search(OBJECTIVE, QUERIES, PT_BR, max_results=5, session_id="sweep-1")

    assert isinstance(r, SearchResponse)
    assert is_degraded(r) is False
    assert r.search_id == "search_abc123"
    assert r.session_id == "sweep-1"
    assert [x.url for x in r.results] == ["https://example.com/a",
                                          "https://example.com/b"]
    first = r.results[0]
    assert (first.title, first.publish_date) == ("A", "2026-08-01")
    assert first.excerpts == ["snippet a"]
    assert first.raw["url"] == "https://example.com/a"   # provenance kept
    assert len(client.calls) == 1


def test_the_sdk_is_called_with_the_shape_parallel_web_accepts():
    t, client, _, _ = tool()

    t.search(OBJECTIVE, QUERIES, PT_BR, max_results=7, session_id="sweep-9",
             source_policy=SourcePolicy(exclude_domains=("spam.example",),
                                        after_date="2026-01-01"),
             max_chars_total=20_000)

    kwargs = client.calls[0]
    assert kwargs["search_queries"] == QUERIES
    assert kwargs["objective"] == OBJECTIVE
    assert kwargs["mode"] == "basic"          # not the SDK's `advanced` default
    assert kwargs["session_id"] == "sweep-9"
    assert kwargs["max_chars_total"] == 20_000
    # location and max_results are advanced settings, not top-level arguments
    assert "location" not in kwargs and "max_results" not in kwargs
    advanced = kwargs["advanced_settings"]
    assert advanced["location"] == "br"       # lowercase ISO 3166-1 alpha-2
    assert advanced["max_results"] == 7
    assert advanced["source_policy"] == {"exclude_domains": ["spam.example"],
                                         "after_date": "2026-01-01"}


def test_every_argument_we_send_is_a_real_parallel_web_parameter():
    """Guards against SDK drift: the fake client would happily accept a
    misspelled or invented parameter, the real one would 422."""
    from parallel.types.advanced_search_settings_param import (
        AdvancedSearchSettingsParam,
    )
    from parallel.types.client_search_params import ClientSearchParams
    from parallel.types.shared_params.source_policy import (
        SourcePolicy as SdkSourcePolicy,
    )

    t, client, _, _ = tool()
    t.search(OBJECTIVE, QUERIES, PT_BR, max_results=5, session_id="sweep-1",
             source_policy=SourcePolicy(exclude_domains=("spam.example",),
                                        include_domains=("news.example",),
                                        after_date="2026-01-01"),
             max_chars_total=20_000)

    kwargs = client.calls[0]
    advanced = kwargs["advanced_settings"]
    assert set(kwargs) <= set(ClientSearchParams.__annotations__)
    assert set(advanced) <= set(AdvancedSearchSettingsParam.__annotations__)
    assert set(advanced["source_policy"]) <= set(SdkSourcePolicy.__annotations__)


def test_language_travels_in_the_query_text_not_a_parameter():
    """WU-04: multilingual input is native; there is no language argument."""
    t, client, _, _ = tool()

    t.search(OBJECTIVE, QUERIES, PT_BR)

    kwargs = client.calls[0]
    assert "language" not in kwargs
    assert "language" not in kwargs["advanced_settings"]
    assert kwargs["search_queries"][1].startswith("anúncio")


def test_results_are_capped_at_max_results():
    client = FakeClient(FakeSearchResult(results=[
        FakeResult(f"https://example.com/{i}") for i in range(10)]))
    t, _, _, _ = tool(client)

    r = t.search(OBJECTIVE, QUERIES, EN_US, max_results=3)

    assert len(r.results) == 3


# ---------------------------------------------------------------- call log

def test_call_log_shows_queries_location_and_result_count(caplog):
    t, _, _, _ = tool()

    with caplog.at_level(logging.INFO, logger="consentinel.parallel_search"):
        t.search(OBJECTIVE, QUERIES, PT_BR, session_id="sweep-1")

    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("parallel_search sdk="))
    assert "sdk=parallel-web api=search" in line     # the SDK, not raw httpx
    assert "location=br" in line and "locale=pt-BR" in line
    assert "results=2" in line
    assert "cache=miss" in line
    assert "mira vance voz clonada" in line
    assert "search_id=search_abc123" in line
    assert "latency_ms=" in line


# ---------------------------------------------------------------- audit

def test_audit_row_per_call_carries_from_cache_and_cache_age():
    t, _, _, audit = tool()

    t.search(OBJECTIVE, QUERIES, PT_BR, session_id="sweep-1")
    t.search(OBJECTIVE, QUERIES, PT_BR, session_id="sweep-1")

    rows = [e for e in audit.events if e.get("event") == "tool_call"]
    assert len(rows) == 2
    assert rows[0]["from_cache"] is False and rows[0]["cache_age_s"] is None
    assert rows[0]["result_count"] == 2
    assert rows[0]["queries"] == QUERIES
    assert rows[0]["location"] == "br"
    assert rows[0]["sdk"] == "parallel-web"
    assert rows[1]["from_cache"] is True
    assert rows[1]["cache_age_s"] is not None


# ---------------------------------------------------------------- cache

def test_second_identical_call_is_served_from_cache_without_touching_the_sdk():
    t, client, _, _ = tool()

    first = t.search(OBJECTIVE, QUERIES, PT_BR)
    second = t.search(OBJECTIVE, QUERIES, PT_BR)

    assert len(client.calls) == 1
    assert [x.url for x in second.results] == [x.url for x in first.results]


def test_query_order_does_not_fragment_the_cache():
    t, client, _, _ = tool()

    t.search(OBJECTIVE, QUERIES, PT_BR)
    t.search(OBJECTIVE, list(reversed(QUERIES)), PT_BR)

    assert len(client.calls) == 1


def test_locale_is_part_of_the_cache_key():
    """Hard rule 8. The same query in another territory is another question."""
    t, client, _, _ = tool()

    t.search(OBJECTIVE, QUERIES, PT_BR)
    t.search(OBJECTIVE, QUERIES, EN_US)

    assert len(client.calls) == 2


def test_session_id_is_not_part_of_the_cache_key():
    t, client, _, _ = tool()

    t.search(OBJECTIVE, QUERIES, PT_BR, session_id="sweep-1")
    t.search(OBJECTIVE, QUERIES, PT_BR, session_id="sweep-2")

    assert len(client.calls) == 1


def test_cached_entry_is_written_with_the_ttl_from_the_policy():
    t, _, cache, _ = tool(cache_ttl_s=6 * 3600)

    t.search(OBJECTIVE, QUERIES, PT_BR)

    (_, (_, _, ttl)), = cache.entries.items()
    assert ttl == 6 * 3600
    assert 6 * 3600 <= 24 * 3600      # inside the band WU-05 specifies


def test_cache_hit_reports_age_on_the_detailed_result():
    t, client, cache, _ = tool()
    warm = t.search_detailed(OBJECTIVE, QUERIES, PT_BR)
    key, = list(cache.entries)
    cache.preload(key, warm.value, age_s=120.0)

    r = t.search_detailed(OBJECTIVE, QUERIES, PT_BR)

    assert r.from_cache is True
    assert r.cache_age_s is not None and r.cache_age_s >= 120.0
    assert len(client.calls) == 1


# ---------------------------------------------------------------- fail safe

def test_failure_returns_degraded_not_an_empty_result_set():
    """Rule 10: a sweep that could not look must not look like one that
    looked and found nothing."""
    client = FakeClient(errors=[RuntimeError("bad gateway body")])
    t, _, _, audit = tool(client, max_attempts=0)

    r = t.search(OBJECTIVE, QUERIES, PT_BR, session_id="sweep-1")

    assert r.results == []
    assert is_degraded(r) is True
    assert "bad gateway body" in (degraded_reason(r) or "")
    assert r.warnings[0]["type"] == DEGRADED_WARNING_TYPE
    assert r.session_id == "sweep-1"
    row = next(e for e in audit.events if e.get("event") == "tool_call")
    assert row["ok"] is False and row["fail_state"] == "degraded"


def test_a_clean_search_that_finds_nothing_is_not_degraded():
    t, _, _, _ = tool(FakeClient(FakeSearchResult(results=[])))

    r = t.search(OBJECTIVE, QUERIES, PT_BR)

    assert r.results == []
    assert is_degraded(r) is False
    assert r.search_id == "search_abc123"


def test_transient_error_is_retried_then_succeeds():
    err = TimeoutError("connection timed out")
    t, client, _, _ = tool(FakeClient(errors=[err]))

    r = t.search_detailed(OBJECTIVE, QUERIES, PT_BR)

    assert r.ok is True
    assert r.attempts == 2
    assert len(client.calls) == 2


def test_auth_failure_is_not_retried():
    class Unauthorized(Exception):
        status_code = 401

    t, client, _, _ = tool(FakeClient(errors=[Unauthorized("bad key"),
                                              Unauthorized("bad key")]))

    r = t.search_detailed(OBJECTIVE, QUERIES, PT_BR)

    assert r.ok is False
    assert r.fail_state is FailState.DEGRADED
    assert len(client.calls) == 1      # retrying a 401 only burns quota


def test_empty_query_list_degrades_without_calling_the_api():
    t, client, _, _ = tool()

    r = t.search(OBJECTIVE, [], PT_BR)

    assert is_degraded(r) is True
    assert "at least one keyword query" in (degraded_reason(r) or "")
    assert client.calls == []


def test_out_of_band_query_shape_warns_but_still_searches(caplog):
    t, client, _, _ = tool()

    with caplog.at_level(logging.WARNING, logger="consentinel.parallel_search"):
        r = t.search(OBJECTIVE, ["mira"], PT_BR)

    assert is_degraded(r) is False
    assert len(client.calls) == 1
    warnings = [rec.getMessage() for rec in caplog.records
                if rec.levelno == logging.WARNING]
    assert any("1 queries" in m for m in warnings)
    assert any("is 1 words" in m for m in warnings)


# ---------------------------------------------------------------- demo mode

def test_demo_mode_serves_the_cache_and_never_calls_the_network():
    t, client, cache, _ = tool()
    warm = t.search_detailed(OBJECTIVE, QUERIES, PT_BR)
    key, = list(cache.entries)
    calls_before = len(client.calls)

    t.demo_mode = True
    r = t.search(OBJECTIVE, QUERIES, PT_BR)

    assert len(client.calls) == calls_before
    assert is_degraded(r) is False
    assert [x.url for x in r.results] == [x.url for x in warm.value.results]
    assert key in cache.entries


def test_demo_mode_cache_miss_degrades_rather_than_calling_out():
    t, client, _, _ = tool(demo_mode=True)

    r = t.search(OBJECTIVE, QUERIES, PT_BR)

    assert client.calls == []
    assert is_degraded(r) is True
    assert "DEMO_MODE" in (degraded_reason(r) or "")


# ---------------------------------------------------------------- contract

def test_module_level_entry_point_matches_the_frozen_signature():
    import inspect
    from importlib import import_module

    from consentinel.tools import contracts
    # import_module, because the package deliberately exports the *function*
    # under this name — see consentinel/tools/__init__.py
    module = import_module("consentinel.tools.parallel_search")

    frozen = inspect.signature(contracts.parallel_search)
    ours = inspect.signature(module.parallel_search)
    assert list(ours.parameters) == list(frozen.parameters)
    assert [p.default for p in ours.parameters.values()] == \
           [p.default for p in frozen.parameters.values()]


def test_tools_package_exports_the_implementation_not_the_stub():
    from consentinel.tools import parallel_search as exported
    from consentinel.tools.parallel_search import parallel_search as impl

    assert exported is impl
    with pytest.raises(NotImplementedError):
        from consentinel.tools.contracts import parallel_search as stub
        stub(OBJECTIVE, QUERIES, PT_BR)
