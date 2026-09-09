"""The sweep runner — the chain, and the publication rule.

The chain itself is covered by the tests for each part. What is only testable
here is the thing the runner adds: **which findings get written to a registry a
public page renders.** Getting that wrong publishes an unauthorised-use verdict
naming a real company, from a demo, on a public URL.

No network: the searching, reading and judging are all injected.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from consentinel import sweep as sweepmod
from consentinel.store.base import Consent, Finding, Performer, PermittedUse


# ------------------------------------------------------- the publication rule


@pytest.mark.parametrize("url,allowed", [
    ("https://consentinel-web-255860737849.us-central1.run.app/demo/listing", True),
    ("https://anything.run.app/x", True),
    ("https://clonevoice.invalid/mira", True),
    ("http://localhost:8081/demo/listing", True),
    ("https://a-real-company.com/listing", False),
    ("https://marketplace.example/mira-voice", False),
    ("https://run.app.attacker.net/x", False),      # suffix must not be spoofable
    ("not a url at all", False),
])
def test_only_addresses_we_control_may_be_published(url, allowed):
    """`.run.app` is ours, `.invalid` can never resolve to a real site.

    The spoofing case matters: `run.app.attacker.net` ends with neither, and a
    naive `in` check would have let it through.
    """
    assert sweepmod.publishable(url) is allowed


def test_a_third_party_candidate_is_counted_and_not_written(monkeypatch):
    """It is found, it is named in the log, and it does not reach the registry.

    Withholding is the default because the repo and the video are public and
    `CLAUDE.md` Demo safety says we do not publish verdicts about real third
    parties. `--allow-third-party` exists for a private namespace.
    """
    store = _FakeStore()
    summary = sweepmod.run(
        store, _performer(), [_grant()],
        deps=_deps(candidates=["https://a-real-company.com/x",
                                "https://ours.run.app/demo/listing"]),
    )

    assert summary.withheld == 1
    assert summary.recorded == 1
    assert [f.url for f in store.findings] == ["https://ours.run.app/demo/listing"]


def test_allowing_third_parties_writes_them(monkeypatch):
    store = _FakeStore()
    summary = sweepmod.run(
        store, _performer(), [_grant()],
        deps=_deps(candidates=["https://a-real-company.com/x"]),
        allow_third_party=True,
    )

    assert summary.withheld == 0
    assert summary.recorded == 1


# ------------------------------------------------------------ what gets stored


def test_a_finding_is_written_once_with_its_verdict(monkeypatch):
    """Not a bare row now and a verdict later.

    A finding on screen with no verdict reads as "checked, nothing wrong" —
    which is the opposite of what an unjudged row means.
    """
    store = _FakeStore()
    sweepmod.run(store, _performer(), [_grant()],
                 deps=_deps(candidates=["https://ours.run.app/demo/listing"]))

    assert len(store.findings) == 1
    finding = store.findings[0]
    assert finding.verdict is not None
    assert finding.evidence_quote, "a decisive verdict must carry its citation"
    assert finding.reasoning


def test_a_page_we_cannot_read_is_recorded_as_unclear(monkeypatch):
    """Fail toward doubt. Silence would leave the page looking unexamined."""
    store = _FakeStore()
    summary = sweepmod.run(
        store, _performer(), [_grant()],
        deps=_deps(candidates=["https://ours.run.app/demo/listing"], readable=False),
    )

    assert summary.refused == 1
    assert store.findings[0].verdict is None
    assert "did not open this page" in store.findings[0].reasoning


def test_a_dry_run_writes_nothing(monkeypatch):
    store = _FakeStore()
    summary = sweepmod.run(store, _performer(), [_grant()],
                           deps=_deps(candidates=["https://ours.run.app/x"]),
                           dry_run=True)

    assert store.findings == []
    assert summary.candidates == 1


# --------------------------------------------------------------------- helpers


def _performer():
    return Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])


def _grant():
    return Consent(id="c1", performer_id="perf_mira", licensee="Halcyon Pictures",
                   permitted_uses=[PermittedUse.VOICE_SYNTH], territories=["US"],
                   valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                   valid_to=datetime(2028, 12, 31, tzinfo=timezone.utc))


class _FakeStore:
    def __init__(self):
        self.findings: list[Finding] = []

    def upsert_finding(self, finding):
        self.findings = [f for f in self.findings if f.id != finding.id] + [finding]
        return finding


def _deps(*, candidates, readable=True):
    """Stub the plan, the search, the fetch, the read and the judgement.

    Swapped in on the module rather than injected, because `run()` builds its
    own collaborators — the alternative would be five constructor arguments
    that exist only for tests.

    Everything is `SimpleNamespace`, not nested classes: a class body inside a
    function cannot see that function's locals, which cost ten minutes here.
    """
    from types import SimpleNamespace as NS

    import consentinel.agents.reconciler as rec
    import consentinel.sweep as m
    from consentinel.harness.ports import HarnessDeps

    plan = NS(locales=(), batches=(), degraded=False)
    urls = list(candidates)
    report = NS(raw_results=len(urls), degraded=False, abort_reason=None,
                batches_run=len(urls), from_cache=0,
                candidates=tuple(m._manual_candidate(u, plan) for u in urls))

    extraction = NS(depicts_named_person=True, person_name="Mira Vance",
                    is_synthetic_claim=True, modality="voice", is_commercial=True,
                    target_territories=["BR"],
                    evidence_quote="AI voice model of Mira Vance", confidence=0.9)
    outcome = NS(ok=readable, reason="" if readable else "timed out",
                 snapshot=object() if readable else None, status=None)
    reading = NS(ok=readable, reason="" if readable else "no answer",
                 extraction=extraction if readable else None)

    def judge(extraction, performer_id, consents, actor=None):
        observation = rec.Observation.from_extraction(extraction, performer_id,
                                                       actor=actor)
        return rec.evaluate(observation, consents)

    m.QueryPlanner = lambda **kw: NS(plan=lambda *a, **k: plan)
    m.TextSweep = lambda **kw: NS(run=lambda *a, **k: report)
    m.PageFetcher = lambda **kw: NS(fetch_detailed=lambda *a, **k: outcome)
    m.Triage = lambda **kw: NS(read=lambda *a, **k: reading)
    m.Reconciler = lambda **kw: NS(for_extraction=judge)
    m.WebRiskCheck = lambda **kw: None
    return HarnessDeps()
