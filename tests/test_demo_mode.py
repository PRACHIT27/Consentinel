"""WU-20 acceptance.

Done when: with networking disabled, a full sweep completes end to end.

"Networking disabled" is enforced rather than assumed — in the demo phase every
client is replaced with one that raises `AssertionError` if it is called at all.
So the pipeline either runs from cache or the test fails; it cannot quietly
reach the network and pass.

The run is the real chain: QueryPlanner's plan → TextSweep → parallel_search →
web_risk → fetch_page → Triage → Reconciler.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest

from consentinel.agents.query_planner import SearchBatch, SearchPlan
from consentinel.agents.reconciler import Reconciler
from consentinel.agents.text_sweep import TextSweep
from consentinel.agents.triage import Triage
from consentinel.demo_mode import (
    ENV_VAR,
    DemoModeMiss,
    describe,
    is_enabled,
    miss,
)
from consentinel.harness import HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.harness.ports import CacheHit
from consentinel.store.base import (
    Consent,
    Finding,
    Locale,
    Performer,
    PermittedUse,
    Verdict,
)
from consentinel.tools.contracts import UrlRisk
from consentinel.tools.fetch_page import FetchRefused, PageFetcher
from consentinel.tools.parallel_search import ParallelSearch
from consentinel.tools.web_risk import WebRiskCheck

MIRA = Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])
PT_BR = Locale(language="pt", region="BR")
URL = "https://vozclone.example/mira"

GRANT = Consent(id="c_aurora", performer_id="perf_mira",
                licensee="Aurora Studios",
                permitted_uses=[PermittedUse.VOICE_SYNTH],
                territories=["US", "CA"])

PAGE_HTML = (b"<html><body><h1>Clone de voz Mira Vance</h1>"
             b"<p>Compre o clone de voz de Mira Vance por R$ 49,90.</p>"
             b"</body></html>")
QUOTE = "Compre o clone de voz de Mira Vance por R$ 49,90."

PLAN = SearchPlan(performer_id=MIRA.id, batches=(
    SearchBatch(objective="find voice copies of Mira Vance in Brazil",
                search_queries=("Mira Vance clone de voz",
                                "Mira Vance voz sintética"),
                locale=PT_BR, modality="voice"),))


# ------------------------------------------------------------------ the cache

class MemoryCache:
    """Stands in for WU-19. One instance shared by every client, the way a
    real cache is."""

    def __init__(self) -> None:
        self.entries: dict[str, Any] = {}

    def get(self, key: str) -> Optional[CacheHit]:
        from datetime import datetime, timezone
        if key not in self.entries:
            return None
        return CacheHit(value=self.entries[key],
                        fetched_at=datetime.now(timezone.utc))

    def put(self, key: str, value: Any, ttl_seconds: Optional[int]) -> None:
        self.entries[key] = value


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, Finding] = {}

    def upsert_finding(self, finding: Finding) -> Finding:
        self.rows[finding.url_hash] = finding
        return finding


# ------------------------------------------------------- live clients (warm)

class LiveSDK:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, **kwargs: Any) -> Any:
        self.calls += 1

        class R:
            search_id = "search_warm"
            session_id = kwargs.get("session_id")
            warnings: list[Any] = []
            results = [type("W", (), {
                "url": URL, "title": "Clone de voz", "publish_date": None,
                "excerpts": ["R$ 49,90"], "model_dump": lambda s: {},
            })()]
        return R()


class LiveRisk:
    def __init__(self) -> None:
        self.calls = 0

    def search_uris(self, *, uri: str, threat_types: Any) -> Any:
        self.calls += 1
        return type("Resp", (), {"threat": None})()


def live_http(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PAGE_HTML,
                          headers={"content-type": "text/html; charset=utf-8"})


def live_model(_instruction: str, _payload: str) -> str:
    return json.dumps({
        "depicts_named_person": True, "person_name": "Mira Vance",
        "is_synthetic_claim": True, "modality": "voice", "is_commercial": True,
        "target_territories": ["BR"], "evidence_quote": QUOTE,
        "confidence": 0.93,
    }, ensure_ascii=False)


# ------------------------------------------------- dead clients (demo phase)

class NetworkIsOff:
    """Anything that touches the network in demo mode fails the test."""

    def search(self, **_kwargs: Any) -> Any:
        raise AssertionError("parallel_search reached the network in DEMO_MODE")

    def search_uris(self, **_kwargs: Any) -> Any:
        raise AssertionError("web_risk reached the network in DEMO_MODE")


def dead_http(request: httpx.Request) -> httpx.Response:
    raise AssertionError("fetch_page reached the network in DEMO_MODE")


def dead_model(_instruction: str, _payload: str) -> str:
    raise AssertionError("Triage reached the model in DEMO_MODE")


# ------------------------------------------------------------------ the rig

def pipeline(cache: MemoryCache, *, demo: bool, store: FakeStore):
    """Every component, sharing one cache and one audit sink."""
    deps = HarnessDeps(cache=cache, audit=MemoryAudit(), metrics=MemoryMetrics())
    def resolve(_host: str) -> list[str]:
        return ["93.184.216.34"]          # no DNS in tests

    risk = WebRiskCheck(deps=deps, client=NetworkIsOff() if demo else LiveRisk(),
                        demo_mode=demo, sleep=lambda _s: None)
    fetcher = PageFetcher(
        deps=deps, risk=risk, demo_mode=demo, resolve=resolve,
        transport=httpx.MockTransport(dead_http if demo else live_http),
        max_attempts=0, sleep=lambda _s: None)
    searcher = ParallelSearch(deps=deps,
                              client=NetworkIsOff() if demo else LiveSDK(),
                              demo_mode=demo, max_attempts=0,
                              sleep=lambda _s: None)
    sweep = TextSweep(store=store, search=searcher, deps=deps, concurrency=1)
    triage = Triage(deps=deps, generate=dead_model if demo else live_model,
                    demo_mode=demo, sleep=lambda _s: None)
    return deps, sweep, fetcher, triage, Reconciler(deps=deps)


# --------------------------------------------------------- the acceptance run

def test_a_full_sweep_completes_end_to_end_with_networking_disabled():
    cache = MemoryCache()
    store = FakeStore()

    # --- warm it once, live
    _, sweep, fetcher, triage, _ = pipeline(cache, demo=False, store=store)
    warm_report = sweep.run(MIRA, PLAN, sweep_id="sweep_warm")
    warm_page = fetcher.fetch(URL, PT_BR)
    warm_reading = triage.read(warm_page, MIRA)
    assert warm_report.findings and warm_reading.ok
    assert cache.entries                       # something was actually stored

    # --- now unplug the network entirely
    _, sweep, fetcher, triage, reconciler = pipeline(cache, demo=True,
                                                     store=FakeStore())

    report = sweep.run(MIRA, PLAN, sweep_id="sweep_demo")
    page = fetcher.fetch(URL, PT_BR)
    reading = triage.read(page, MIRA)
    verdict = reconciler.for_extraction(reading.extraction, MIRA.id, [GRANT],
                                        actor="VozClone Studio")

    assert report.degraded is False
    assert len(report.findings) == 1
    assert report.from_cache == 1
    assert page.text.startswith("Clone de voz Mira Vance")
    assert reading.ok is True
    assert reading.from_cache is True
    assert reading.extraction.evidence_quote == QUOTE
    assert verdict.verdict is Verdict.UNAUTHORIZED       # BR is outside US/CA
    assert verdict.territories_outside == ("BR",)
    assert verdict.citation == QUOTE


def test_the_demo_run_touches_no_client_at_all():
    """Belt and braces: the dead clients raise, so a single live call would
    surface as an error rather than a silent success."""
    cache = MemoryCache()
    store = FakeStore()
    _, sweep, fetcher, triage, _ = pipeline(cache, demo=False, store=store)
    sweep.run(MIRA, PLAN)
    triage.read(fetcher.fetch(URL, PT_BR), MIRA)

    _, sweep, fetcher, triage, _ = pipeline(cache, demo=True, store=FakeStore())

    sweep.run(MIRA, PLAN)                  # would raise AssertionError if it called out
    triage.read(fetcher.fetch(URL, PT_BR), MIRA)


# ------------------------------------------------ a cold cache, loudly refused

def test_a_cold_cache_refuses_every_client_rather_than_reaching_out():
    cache = MemoryCache()
    _, sweep, fetcher, triage, _ = pipeline(cache, demo=True, store=FakeStore())

    report = sweep.run(MIRA, PLAN)
    reading = triage.read(
        __import__("consentinel.tools.contracts", fromlist=["PageSnapshot"])
        .PageSnapshot(url=URL, text="Compre o clone de voz de Mira Vance."),
        MIRA)

    assert report.degraded is True             # not an empty clean result
    assert report.findings == ()
    assert reading.ok is False
    assert ENV_VAR in (reading.reason or "")
    with pytest.raises(FetchRefused) as caught:
        fetcher.fetch(URL, PT_BR)
    assert ENV_VAR in str(caught.value)


def test_the_refusal_says_how_to_fix_it():
    """A message that only says "cache miss" sends someone reading logs at 1am
    to the wrong place."""
    error = miss("parallel_search", "parallel_search:abc123")

    assert isinstance(error, DemoModeMiss)
    assert "warm_demo_cache.py" in str(error)
    assert "parallel_search:abc123" in str(error)
    assert f"unset {ENV_VAR}" in str(error)


def test_a_demo_mode_miss_is_never_retried():
    """Retrying will not put the answer in the cache."""
    from consentinel.reliability import ErrorClass, classify

    assert classify(miss("x", "y")) is ErrorClass.PERMANENT


def test_web_risk_in_demo_mode_fails_closed_rather_than_opening_the_page():
    """A cold risk check is unanswerable, and unanswerable means skip."""
    deps = HarnessDeps(cache=MemoryCache(), audit=MemoryAudit())
    risk = WebRiskCheck(deps=deps, client=NetworkIsOff(), demo_mode=True,
                        sleep=lambda _s: None)

    result = risk.check(URL)

    assert isinstance(result, UrlRisk)
    assert result.safe is False                # not permission, in either mode


# ------------------------------------------------------------------- the flag

def test_the_flag_reads_the_environment_but_an_override_wins(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert is_enabled() is False
    for truthy in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv(ENV_VAR, truthy)
        assert is_enabled() is True
        assert is_enabled(False) is False      # explicit beats ambient
    monkeypatch.setenv(ENV_VAR, "false")
    assert is_enabled() is False
    assert is_enabled(True) is True


def test_describe_is_readable_in_a_startup_log():
    assert "cache only, no external calls" in describe(True)
    assert "live calls allowed" in describe(False)


def test_every_client_takes_the_same_three_state_flag():
    """`True`, `False`, or `None` meaning "read the environment". Tests always
    pass an explicit value, so no test depends on your .env."""
    for component in (ParallelSearch, PageFetcher, WebRiskCheck, Triage):
        assert "demo_mode" in component.__dataclass_fields__
        assert component.__dataclass_fields__["demo_mode"].default is None
