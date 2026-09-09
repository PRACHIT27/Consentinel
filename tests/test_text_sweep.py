"""WU-07 acceptance.

Done when: running the same sweep twice produces zero duplicate rows.

The Store is faked, but faked *faithfully*: keyed on `url_hash`, preserving
`first_seen` and refreshing `last_checked`, exactly as `FirestoreStore` does.
A fake that skipped that would make the idempotency test meaningless.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from consentinel.agents.query_planner import SearchBatch, SearchPlan, deterministic_plan
from consentinel.agents.text_sweep import (
    TextSweep,
    normalise_url,
    url_hash,
)
from consentinel.harness import HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.store.base import (
    DiscoveredVia,
    Finding,
    FindingStatus,
    Locale,
    Performer,
)
from consentinel.tools.parallel_search import ParallelSearch

MIRA = Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])
PT_BR = Locale(language="pt", region="BR")
EN_US = Locale(language="en", region="US")


# ------------------------------------------------------------------- fakes

class FakeStore:
    """Mirrors FirestoreStore.upsert_finding: idempotent on url_hash,
    first_seen survives, last_checked refreshes."""

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.rows: dict[str, Finding] = {}
        self.upserts = 0
        self.fail_on = fail_on

    def upsert_finding(self, finding: Finding) -> Finding:
        self.upserts += 1
        if finding.url in self.fail_on:
            raise RuntimeError("firestore unavailable")
        now = datetime.now(timezone.utc)
        prior = self.rows.get(finding.url_hash)
        finding.first_seen = (prior.first_seen if prior else None) or now
        finding.last_checked = now
        self.rows[finding.url_hash] = finding
        return finding


class FakeSDK:
    """Returns one canned response per batch, in call order."""

    def __init__(self, *pages_per_batch: list[str], error_batches: tuple[int, ...] = ()) -> None:
        self.pages_per_batch = list(pages_per_batch)
        self.error_batches = error_batches
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> Any:
        index = len(self.calls)
        self.calls.append(kwargs)
        if index in self.error_batches:
            raise RuntimeError("503 upstream")
        urls = self.pages_per_batch[min(index, len(self.pages_per_batch) - 1)]

        class R:
            search_id = f"search_{index}"
            session_id = kwargs.get("session_id")
            warnings = []
            results = [type("W", (), {
                "url": u, "title": "T", "publish_date": None,
                "excerpts": ["snippet"], "model_dump": lambda s: {},
            })() for u in urls]
        return R()


def sweep(*pages_per_batch: list[str], store: Optional[FakeStore] = None,
          error_batches: tuple[int, ...] = (), **kw: Any
          ) -> tuple[TextSweep, FakeStore, FakeSDK, MemoryAudit]:
    store = store or FakeStore()
    sdk = FakeSDK(*pages_per_batch, error_batches=error_batches)
    audit = MemoryAudit()
    deps = HarnessDeps(audit=audit, metrics=MemoryMetrics())
    searcher = ParallelSearch(deps=deps, client=sdk, demo_mode=False,
                              max_attempts=0, sleep=lambda _s: None)
    kw.setdefault("concurrency", 2)
    return TextSweep(store=store, search=searcher, deps=deps, **kw), store, sdk, audit


def plan_of(*batches: tuple[Locale, str, tuple[str, ...]], degraded: bool = False) -> SearchPlan:
    return SearchPlan(
        performer_id=MIRA.id, degraded=degraded,
        batches=tuple(SearchBatch(objective=f"find {modality} copies",
                                  search_queries=queries, locale=locale,
                                  modality=modality)
                      for locale, modality, queries in batches))


ONE_BATCH = plan_of((PT_BR, "voice", ("Mira Vance clone de voz",
                                      "Mira Vance voz sintética")))
TWO_BATCHES = plan_of(
    (PT_BR, "voice", ("Mira Vance clone de voz", "Mira Vance voz sintética")),
    (EN_US, "voice", ("Mira Vance AI voice clone", "Mira Vance synthetic voice")),
)


# --------------------------------------------------------- normalisation

def test_fragment_query_order_case_and_tracking_params_collapse():
    """One page must not arrive as three findings (FR-2.5)."""
    variants = [
        "https://Example.com/mira?b=2&a=1#reviews",
        "https://example.com/mira?a=1&b=2",
        "https://example.com:443/mira?b=2&a=1&utm_source=twitter",
        "https://example.com/mira/?a=1&b=2&fbclid=xyz",
    ]

    hashes = {url_hash(u) for u in variants}

    assert len(hashes) == 1
    assert normalise_url(variants[0]) == "https://example.com/mira?a=1&b=2"


def test_normalisation_keeps_the_distinctions_that_matter():
    assert url_hash("https://example.com/a") != url_hash("https://example.com/b")
    assert url_hash("https://example.com/a?page=1") != \
           url_hash("https://example.com/a?page=2")
    # www and the bare host are NOT merged: a wrong merge hides a finding
    assert url_hash("https://www.example.com/a") != url_hash("https://example.com/a")
    # path case can be significant on a case-sensitive server
    assert url_hash("https://example.com/Mira") != url_hash("https://example.com/mira")


def test_normalisation_survives_junk():
    assert normalise_url("") == ""
    assert normalise_url("   ") == ""
    assert normalise_url("example.com/mira") == "https://example.com/mira"


# ------------------------------------------------------------- acceptance

def test_running_the_same_sweep_twice_produces_zero_duplicate_rows():
    """The WU-07 acceptance line."""
    pages = ["https://a.example/mira", "https://b.example/mira"]
    t, store, _, _ = sweep(pages, pages)

    first = t.run(MIRA, TWO_BATCHES)
    second = t.run(MIRA, TWO_BATCHES, sweep_id="sweep_second")

    assert len(store.rows) == 2                 # not 4
    assert len(first.findings) == len(second.findings) == 2
    assert store.upserts == 4                   # written twice, stored once


def test_a_re_sweep_refreshes_last_checked_and_keeps_first_seen():
    """Otherwise "when did we first see this" resets on every run and
    scheduled monitoring means nothing."""
    t, store, _, _ = sweep(["https://a.example/mira"])
    t.run(MIRA, ONE_BATCH)
    row = next(iter(store.rows.values()))
    original_first_seen = row.first_seen
    row.first_seen = original_first_seen - timedelta(days=3)   # pretend it is old
    original_first_seen = row.first_seen

    t.run(MIRA, ONE_BATCH, sweep_id="sweep_2")

    row = next(iter(store.rows.values()))
    assert row.first_seen == original_first_seen
    assert row.last_checked > original_first_seen


def test_the_same_url_from_two_batches_becomes_one_finding():
    shared = ["https://a.example/mira"]
    t, _, _, _ = sweep(shared, shared)

    report = t.run(MIRA, TWO_BATCHES)

    assert report.raw_results == 2
    assert report.duplicates_collapsed == 1
    assert len(report.findings) == 1


# ---------------------------------------------------------------- fields

def test_discovered_locale_records_where_we_searched_from():
    t, _, _, _ = sweep(["https://br.example/mira"], ["https://us.example/mira"])

    report = t.run(MIRA, TWO_BATCHES)

    by_url = {f.url: f for f in report.findings}
    assert by_url["https://br.example/mira"].discovered_locale == "pt-BR"
    assert by_url["https://us.example/mira"].discovered_locale == "en-US"


def test_target_territories_and_modality_are_left_for_triage():
    """We searched a modality; we have not established the page depicts one.
    Asserting it here would put an unverified claim in the registry."""
    t, _, _, _ = sweep(["https://a.example/mira"])

    finding = t.run(MIRA, ONE_BATCH).findings[0]

    assert finding.target_territories == []
    assert finding.modality is None
    assert finding.verdict is None
    assert finding.status is FindingStatus.NEW
    assert finding.discovered_via is DiscoveredVia.TEXT


def test_the_finding_id_is_deterministic_so_upsert_stays_idempotent():
    t, _, _, _ = sweep(["https://a.example/mira"])

    finding = t.run(MIRA, ONE_BATCH).findings[0]

    assert finding.id == finding.url_hash == url_hash("https://a.example/mira")


def test_excerpts_ride_in_the_report_but_never_into_the_registry():
    """Excerpts are LLM-selected and truncated; evidence is our own snapshot."""
    t, _, _, _ = sweep(["https://a.example/mira"])

    report = t.run(MIRA, ONE_BATCH)

    assert report.candidates[0].excerpts == ("snippet",)
    assert not hasattr(report.findings[0], "excerpts")


# ------------------------------------------------------------ the budget

def test_the_plan_budget_alone_keeps_a_sweep_inside_25():
    """Two layers, and the outer one should never be reached: the plan asks for
    25 // batches results each, so a full sweep lands under the cap by
    construction."""
    t, store, _, _ = sweep([f"https://a.example/{i}" for i in range(20)],
                           [f"https://b.example/{i}" for i in range(20)])

    report = t.run(MIRA, TWO_BATCHES)

    assert TWO_BATCHES.results_per_batch == 12
    assert len(report.findings) == 24        # 12 + 12, both batches distinct
    assert report.capped is False            # nothing had to be dropped
    assert len(store.rows) == 24


def test_the_sweep_cap_drops_the_overflow_when_a_batch_over_delivers():
    """Belt to the plan's braces: if a provider ignores max_results, or a
    future planner miscounts, the sweep still stops."""
    many = [f"https://a.example/{i}" for i in range(40)]
    t, store, _, _ = sweep(many, max_candidates=10)

    report = t.run(MIRA, ONE_BATCH)

    assert len(report.findings) == 10
    assert report.capped is True
    assert len(store.rows) == 10


def test_the_cap_is_reproducible_not_a_race():
    """Batches run concurrently, but results are collected in plan order, so
    two identical sweeps keep the same candidates."""
    first_batch = [f"https://a.example/{i}" for i in range(20)]
    second_batch = [f"https://b.example/{i}" for i in range(20)]
    t1, _, _, _ = sweep(first_batch, second_batch, max_candidates=15)
    t2, _, _, _ = sweep(first_batch, second_batch, max_candidates=15)

    a = t1.run(MIRA, TWO_BATCHES)
    b = t2.run(MIRA, TWO_BATCHES)

    assert [f.url for f in a.findings] == [f.url for f in b.findings]
    assert len(a.findings) == 15
    assert a.capped is True


def test_max_results_per_batch_comes_from_the_plan_budget():
    t, _, sdk, _ = sweep(["https://a.example/mira"], ["https://b.example/mira"])
    plan = deterministic_plan(MIRA)
    t.max_candidates = 25

    t.run(MIRA, plan)

    assert len(sdk.calls) == len(plan.batches)
    assert sdk.calls[0]["advanced_settings"]["max_results"] == plan.results_per_batch


def test_every_batch_reaches_the_sdk_with_its_own_locale_and_the_sweep_id():
    t, _, sdk, _ = sweep(["https://a.example/1"], ["https://b.example/2"])

    t.run(MIRA, TWO_BATCHES, sweep_id="sweep_abc")

    locations = [c["advanced_settings"]["location"] for c in sdk.calls]
    assert sorted(locations) == ["br", "us"]
    assert {c["session_id"] for c in sdk.calls} == {"sweep_abc"}


# -------------------------------------------------------------- fail safe

def test_one_dead_batch_degrades_the_sweep_but_the_others_still_count():
    t, store, _, _ = sweep(["https://a.example/1"], ["https://b.example/2"],
                           error_batches=(0,))

    report = t.run(MIRA, TWO_BATCHES)

    assert report.batches_run == 1
    assert report.batches_degraded == ("pt-BR/voice",)
    assert report.degraded is True
    assert len(report.findings) == 1          # the healthy batch still landed
    assert len(store.rows) == 1


def test_an_empty_sweep_that_worked_is_not_degraded():
    """"We looked and found nothing" must not read like "we could not look"."""
    t, _, _, _ = sweep([])

    report = t.run(MIRA, ONE_BATCH)

    assert report.findings == ()
    assert report.degraded is False
    assert report.batches_run == 1


def test_a_degraded_plan_travels_into_the_report():
    t, _, _, _ = sweep(["https://a.example/1"])
    plan = plan_of((PT_BR, "voice", ("Mira Vance clone de voz",
                                     "Mira Vance voz sintética")), degraded=True)

    report = t.run(MIRA, plan)

    assert report.plan_degraded is True
    assert report.degraded is True
    assert len(report.findings) == 1          # the sweep still ran


def test_a_failed_write_is_counted_and_does_not_kill_the_sweep():
    store = FakeStore(fail_on=("https://b.example/2",))
    t, store, _, _ = sweep(["https://a.example/1", "https://b.example/2"],
                           store=store)

    report = t.run(MIRA, ONE_BATCH)

    assert report.write_failures == 1
    assert len(report.findings) == 1
    assert report.degraded is True


def test_an_empty_plan_produces_an_empty_sweep_rather_than_an_error():
    t, _, sdk, _ = sweep(["https://a.example/1"])

    report = t.run(MIRA, SearchPlan(performer_id=MIRA.id, batches=()))

    assert report.findings == ()
    assert sdk.calls == []
    assert report.batches_planned == 0


# ------------------------------------------------------------------ trail

def test_one_audit_row_per_sweep_alongside_wu_05s_row_per_call():
    t, _, _, audit = sweep(["https://a.example/1"], ["https://b.example/2"])

    t.run(MIRA, TWO_BATCHES, sweep_id="sweep_abc")

    sweeps = [e for e in audit.events if e.get("event") == "sweep"]
    calls = [e for e in audit.events if e.get("event") == "tool_call"]
    assert len(sweeps) == 1
    assert len(calls) == 2                    # WU-05 still logs every call
    row = sweeps[0]
    assert row["sweep_id"] == "sweep_abc"
    assert row["subject_id"] == "perf_mira"
    assert row["findings"] == 2
    assert row["degraded"] is False


def test_the_summary_log_line_reads_at_a_glance(caplog):
    t, _, _, _ = sweep(["https://a.example/1"])

    with caplog.at_level(logging.INFO, logger="consentinel.text_sweep"):
        t.run(MIRA, ONE_BATCH, sweep_id="sweep_abc")

    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("TextSweep sweep="))
    assert "performer=perf_mira" in line
    assert "batches=1/1" in line
    assert "findings=1" in line
    assert "degraded=False" in line


def test_a_cache_hit_is_visible_in_the_report():
    """DEMO_MODE runs the whole pipeline from cache; the report must show it."""
    class Cache(dict):
        def get(self, key):
            from consentinel.harness.ports import CacheHit
            if key not in self:
                return None
            return CacheHit(value=self[key], fetched_at=datetime.now(timezone.utc))

        def put(self, key, value, ttl):
            self[key] = value

    audit = MemoryAudit()
    deps = HarnessDeps(cache=Cache(), audit=audit)
    searcher = ParallelSearch(deps=deps, client=FakeSDK(["https://a.example/1"]),
                              demo_mode=False, sleep=lambda _s: None)
    t = TextSweep(store=FakeStore(), search=searcher, deps=deps)

    t.run(MIRA, ONE_BATCH)
    second = t.run(MIRA, ONE_BATCH, sweep_id="sweep_2")

    assert second.from_cache == 1
    assert len(second.findings) == 1


# ------------------------------------------------- end to end with WU-06

def test_a_real_plan_from_wu_06_sweeps_end_to_end():
    """WU-06 -> WU-05 -> WU-07 with nothing hand-written in between."""
    pages = [f"https://loja.example/{i}" for i in range(4)]
    t, store, sdk, _ = sweep(pages)
    plan = deterministic_plan(MIRA)

    report = t.run(MIRA, plan)

    assert len(sdk.calls) == len(plan.batches) >= 5
    assert report.degraded is False
    # every batch returns the same pages, and each batch may only ask for
    # results_per_batch of them — so the sweep collapses to that many rows
    unique = min(len(pages), plan.results_per_batch)
    assert len(store.rows) == unique
    assert report.duplicates_collapsed == report.raw_results - unique
    assert {f.discovered_locale for f in report.findings} <= \
           {str(loc) for loc in plan.locales}
