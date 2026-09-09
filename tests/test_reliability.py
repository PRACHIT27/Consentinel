"""WU-14 acceptance.

Done when: a 401 is not retried, and N consecutive failures mark the sweep
degraded rather than producing an empty clean-looking result. Both are here —
the second twice, once at the `SweepGuard` level and once through a real
`TextSweep`.

The governing rule under test throughout: every failure resolves toward doubt,
never toward permission. A compliance tool that fails quietly is worse than no
tool, because somebody signs off on its output.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from consentinel.agents.query_planner import SearchBatch, SearchPlan
from consentinel.agents.text_sweep import TextSweep
from consentinel.harness import HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.reliability import (
    BACKOFF_CAP_S,
    BREAKER_THRESHOLD,
    MAX_ATTEMPTS,
    POLICY,
    CircuitBreaker,
    CircuitOpen,
    ErrorClass,
    SweepGuard,
    backoff_seconds,
    call,
    classify,
    default_breaker,
    record_tool_call,
)
from consentinel.store.base import Finding, Locale, Performer
from consentinel.tools.parallel_search import ParallelSearch

MIRA = Performer(id="perf_mira", name="Mira Vance")
PT_BR = Locale(language="pt", region="BR")


def http_error(status: int) -> Exception:
    error = RuntimeError(f"HTTP {status}")
    error.status_code = status      # type: ignore[attr-defined]
    return error


def deps() -> HarnessDeps:
    return HarnessDeps(audit=MemoryAudit(), metrics=MemoryMetrics())


# ------------------------------------------------------- classification

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status):
    assert classify(http_error(status)) is ErrorClass.TRANSIENT


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 409, 422])
def test_permanent_statuses_are_not(status):
    assert classify(http_error(status)) is ErrorClass.PERMANENT


@pytest.mark.parametrize("message", [
    "connection reset by peer", "operation timed out",
    "service temporarily unavailable", "deadline exceeded",
])
def test_transient_shapes_are_recognised_without_a_status_code(message):
    assert classify(RuntimeError(message)) is ErrorClass.TRANSIENT


def test_an_unrecognised_error_is_permanent():
    """Retried three times, an unknown error costs three times as much and
    tells you nothing new."""
    assert classify(RuntimeError("something odd")) is ErrorClass.PERMANENT


def test_the_published_policy_matches_the_code():
    """The UI and the docs read these numbers; they must be the real ones."""
    assert POLICY["transient"]["attempts"] == MAX_ATTEMPTS == 3
    assert POLICY["transient"]["cap_s"] == BACKOFF_CAP_S == 30.0
    assert POLICY["permanent"]["attempts"] == 1
    assert POLICY["semantic"]["repairs"] == 1
    assert 401 in POLICY["permanent"]["examples"]
    assert 429 in POLICY["transient"]["examples"]


# ----------------------------------------------------------------- backoff

def test_backoff_grows_and_is_capped():
    import random

    rng = random.Random(7)
    biggest = [backoff_seconds(attempt, rng=rng) for attempt in range(1, 12)]

    assert all(0.0 <= value <= BACKOFF_CAP_S for value in biggest)
    assert max(biggest) > 1.0                 # it does grow


def test_backoff_uses_full_jitter():
    """Several agents retrying in lockstep is how a rate limit becomes a
    thundering herd."""
    import random

    values = {backoff_seconds(3, rng=random.Random(seed)) for seed in range(20)}

    assert len(values) > 15                   # not a fixed schedule


# -------------------------------------------------------- the 401 acceptance

def test_a_401_is_not_retried():
    """The headline acceptance condition. Retrying a wrong key thirty times
    only burns quota."""
    attempts: list[int] = []

    def unauthorized():
        attempts.append(1)
        raise http_error(401)

    with pytest.raises(RuntimeError):
        call(unauthorized, provider="parallel", deps=deps(),
             sleep=lambda _s: None)

    assert len(attempts) == 1


def test_a_503_is_retried_up_to_the_attempt_limit():
    attempts: list[int] = []

    def flaky():
        attempts.append(1)
        raise http_error(503)

    with pytest.raises(RuntimeError):
        call(flaky, provider="parallel", deps=deps(), sleep=lambda _s: None)

    assert len(attempts) == MAX_ATTEMPTS


def test_a_transient_failure_that_recovers_returns_the_value():
    calls: list[int] = []

    def recovers():
        calls.append(1)
        if len(calls) < 2:
            raise http_error(503)
        return "ok"

    assert call(recovers, provider="parallel", deps=deps(),
                sleep=lambda _s: None) == "ok"
    assert len(calls) == 2


def test_the_caller_decides_which_flavour_of_doubt_a_failure_becomes():
    """`call` raises rather than swallowing: only the caller knows whether it
    is holding a finding, an asset or a sweep."""
    with pytest.raises(RuntimeError):
        call(lambda: (_ for _ in ()).throw(http_error(404)),
             provider="parallel", deps=deps(), sleep=lambda _s: None)


# ------------------------------------------------------------- the breaker

def test_the_breaker_opens_after_n_consecutive_failures():
    breaker = default_breaker()

    for _ in range(BREAKER_THRESHOLD):
        breaker.record_failure("parallel")

    assert breaker.is_open("parallel") is True
    with pytest.raises(CircuitOpen):
        breaker.guard("parallel")


def test_a_success_resets_the_count():
    breaker = default_breaker()

    for _ in range(BREAKER_THRESHOLD - 1):
        breaker.record_failure("parallel")
    breaker.record_success("parallel")
    breaker.record_failure("parallel")

    assert breaker.is_open("parallel") is False


def test_one_provider_failing_does_not_open_another():
    breaker = default_breaker()

    for _ in range(BREAKER_THRESHOLD):
        breaker.record_failure("parallel")

    assert breaker.is_open("gemini") is False


def test_an_open_breaker_refuses_before_spending_anything():
    breaker = default_breaker()
    for _ in range(BREAKER_THRESHOLD):
        breaker.record_failure("parallel")
    called: list[int] = []

    with pytest.raises(CircuitOpen):
        call(lambda: called.append(1), provider="parallel", deps=deps(),
             breaker=breaker, sleep=lambda _s: None)

    assert called == []


# ------------------------------------------------- the audit row WU-14 asks for

def test_the_audit_row_carries_attempt_number_and_latency():
    d = deps()
    calls: list[int] = []

    def recovers():
        calls.append(1)
        if len(calls) < 3:
            raise http_error(503)
        return "ok"

    call(recovers, provider="parallel", deps=d, sleep=lambda _s: None,
         subject_id="perf_mira", tokens=1234)

    row = next(e for e in d.audit.events if e.get("event") == "tool_call")
    tool_call = row["tool_calls"][0]
    assert tool_call["attempts"] == 3
    assert tool_call["retry_count"] == 2
    assert tool_call["latency_s"] >= 0
    assert tool_call["tokens"] == 1234
    assert row["ok"] is True
    assert row["subject_id"] == "perf_mira"


def test_a_failure_is_audited_with_its_error_class():
    d = deps()

    with pytest.raises(RuntimeError):
        call(lambda: (_ for _ in ()).throw(http_error(401)),
             provider="parallel", deps=d, sleep=lambda _s: None)

    row = next(e for e in d.audit.events if e.get("event") == "tool_call")
    assert row["ok"] is False
    assert row["error_class"] == "permanent"
    assert "401" in row["reason"]


def test_record_tool_call_can_be_used_directly_by_cached_paths():
    d = deps()

    record_tool_call(d, provider="fetch_page", attempts=0, latency_s=0.01,
                     ok=True, from_cache=True, cache_age_s=120.0)

    row = d.audit.events[-1]["tool_calls"][0]
    assert row["from_cache"] is True and row["cache_age_s"] == 120.0


# --------------------------------------- a sweep that could not look, visibly

def test_the_guard_says_we_could_not_look_rather_than_we_found_nothing():
    """The distinction the whole module exists for."""
    guard = SweepGuard(provider="parallel",
                       breaker=CircuitBreaker(threshold=3))

    for _ in range(3):
        guard.record(ok=False)

    assert guard.circuit_open is True
    assert guard.should_abort is True
    assert guard.degraded is True
    assert "could not look" in guard.reason


def test_a_healthy_sweep_has_nothing_to_say():
    guard = SweepGuard()

    for _ in range(4):
        guard.record(ok=True)

    assert guard.degraded is False
    assert guard.reason is None
    assert guard.should_abort is False


def test_a_partial_sweep_says_partial_not_clean():
    guard = SweepGuard(provider="parallel",
                       breaker=CircuitBreaker(threshold=5))

    guard.record(ok=True)
    guard.record(ok=False)
    guard.record(ok=True)

    assert guard.degraded is True
    assert "partial, not clean" in guard.reason
    assert guard.should_abort is False        # one bad batch is not an outage


def test_the_guard_reports_where_it_gave_up():
    guard = SweepGuard(provider="parallel", breaker=CircuitBreaker(threshold=2))

    guard.record(ok=True)
    guard.record(ok=False)
    guard.record(ok=False)

    assert guard.as_dict()["aborted_after"] == 2
    assert guard.as_dict()["batches_ok"] == 1
    assert guard.as_dict()["circuit_open"] is True


# ------------------------------------------- and the same thing through a sweep

class DeadSDK:
    """Every call fails, the way a provider outage looks from here."""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise http_error(503)


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, Finding] = {}

    def upsert_finding(self, finding: Finding) -> Finding:
        self.rows[finding.url_hash] = finding
        return finding


def plan_of(n: int) -> SearchPlan:
    return SearchPlan(performer_id=MIRA.id, batches=tuple(
        SearchBatch(objective="find voice copies",
                    search_queries=("Mira Vance clone de voz",
                                    "Mira Vance voz sintética"),
                    locale=PT_BR, modality="voice")
        for _ in range(n)))


def test_a_dead_provider_aborts_the_sweep_instead_of_returning_it_clean():
    """The second acceptance condition, end to end: N consecutive failures mark
    the sweep degraded rather than producing an empty clean-looking result."""
    sdk = DeadSDK()
    store = FakeStore()
    d = deps()
    searcher = ParallelSearch(deps=d, client=sdk, demo_mode=False,
                              max_attempts=0, sleep=lambda _s: None)
    sweep = TextSweep(store=store, search=searcher, deps=d, concurrency=1,
                      abort_after=2)

    report = sweep.run(MIRA, plan_of(8))

    assert report.findings == ()
    assert report.degraded is True                 # not a clean empty result
    assert report.aborted is True
    assert "could not look" in report.abort_reason
    assert sdk.calls == 2                          # stopped spending after two
    assert len(report.batches_degraded) == 8       # every batch accounted for
    assert any("skipped" in b for b in report.batches_degraded)


def test_an_empty_but_healthy_sweep_is_still_clean():
    """The other half of the distinction: found nothing, and said so."""
    class QuietSDK:
        def search(self, **_kwargs: Any) -> Any:
            class R:
                search_id = "search_1"
                session_id = "s"
                warnings: list[Any] = []
                results: list[Any] = []
            return R()

    d = deps()
    searcher = ParallelSearch(deps=d, client=QuietSDK(), demo_mode=False,
                              sleep=lambda _s: None)
    sweep = TextSweep(store=FakeStore(), search=searcher, deps=d)

    report = sweep.run(MIRA, plan_of(3))

    assert report.findings == ()
    assert report.degraded is False
    assert report.aborted is False
    assert report.abort_reason is None


def test_the_abort_is_logged_and_audited(caplog):
    sdk = DeadSDK()
    d = deps()
    searcher = ParallelSearch(deps=d, client=sdk, demo_mode=False,
                              max_attempts=0, sleep=lambda _s: None)
    sweep = TextSweep(store=FakeStore(), search=searcher, deps=d,
                      concurrency=1, abort_after=2)

    with caplog.at_level(logging.WARNING, logger="consentinel.text_sweep"):
        sweep.run(MIRA, plan_of(5))

    assert any("could not look" in r.getMessage() for r in caplog.records)
    row = next(e for e in d.audit.events if e.get("event") == "sweep")
    assert row["aborted"] is True
    assert "could not look" in row["abort_reason"]
