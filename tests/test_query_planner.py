"""WU-06 acceptance.

Done when: the plan contains genuinely non-English queries across 5+ locales,
and every batch holds 2-3 queries of 3-6 words.

No model is called. The ADK agent is constructed but never run, and the model
call itself is injected — `QueryPlanner(generate=...)`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import pytest

from consentinel.agents.query_planner import (
    CANDIDATE_BUDGET,
    MAX_BATCHES,
    MIN_LANGUAGES,
    MIN_LOCALES,
    QueryPlanner,
    SearchBatch,
    SearchPlan,
    build_instruction,
    build_payload,
    deterministic_plan,
    iter_search_calls,
    parse_plan,
    validate_plan,
    word_bounds,
)
from consentinel.harness import (
    FailState,
    HarnessDeps,
    MemoryAudit,
    MemoryMetrics,
    ValidationError,
)
from consentinel.store.base import Consent, Locale, Performer, PermittedUse

MIRA = Performer(id="perf_mira", name="Mira Vance",
                 aliases=["M. Vance", "Mira V."])

GRANT_PT = Consent(id="c1", performer_id="perf_mira", licensee="Aurora Studios",
                   permitted_uses=[PermittedUse.VOICE_SYNTH],
                   territories=["PT"])


def deps(**kw: Any) -> HarnessDeps:
    kw.setdefault("audit", MemoryAudit())
    kw.setdefault("metrics", MemoryMetrics())
    return HarnessDeps(**kw)


class MemoryCache:
    def __init__(self) -> None:
        self.entries: dict[str, Any] = {}

    def get(self, key: str):
        from datetime import datetime, timezone

        from consentinel.harness.ports import CacheHit
        if key not in self.entries:
            return None
        return CacheHit(value=self.entries[key], fetched_at=datetime.now(timezone.utc))

    def put(self, key: str, value: Any, ttl_seconds: Optional[int]) -> None:
        self.entries[key] = value


def model_plan(batches: Optional[list[dict[str, Any]]] = None) -> str:
    """A well-formed model answer: 5 locales, 5 languages, in-language queries."""
    return json.dumps({"batches": batches if batches is not None else [
        {"objective": "Find AI voice clones of Mira Vance sold in the US.",
         "search_queries": ["Mira Vance AI voice clone",
                            "Mira Vance synthetic voice"],
         "language": "en", "region": "US", "modality": "voice"},
        {"objective": "Encontrar clones de voz de Mira Vance no Brasil.",
         "search_queries": ["Mira Vance clone de voz",
                            "Mira Vance voz sintética"],
         "language": "pt", "region": "BR", "modality": "voice"},
        {"objective": "Encontrar clones de voz de Mira Vance en México.",
         "search_queries": ["Mira Vance clon de voz",
                            "Mira Vance voz sintética"],
         "language": "es", "region": "MX", "modality": "voice"},
        {"objective": "日本でのミラ・ヴァンスのAI音声を探す。",
         "search_queries": ["Mira Vance AI音声クローン", "Mira Vance 合成音声"],
         "language": "ja", "region": "JP", "modality": "voice"},
        {"objective": "भारत में मीरा वांस की एआई आवाज खोजें।",
         "search_queries": ["Mira Vance एआई आवाज क्लोन",
                            "Mira Vance सिंथेटिक आवाज"],
         "language": "hi", "region": "IN", "modality": "voice"},
    ]}, ensure_ascii=False)


class FakeModel:
    """Returns canned answers in order; records what it was asked."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers) or [model_plan()]
        self.calls: list[tuple[str, str]] = []

    def __call__(self, instruction: str, payload: str) -> str:
        self.calls.append((instruction, payload))
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


# ------------------------------------------------------- deterministic plan

def test_deterministic_plan_meets_fr_2_2():
    plan = deterministic_plan(MIRA)

    assert len(plan.locales) >= MIN_LOCALES
    assert len(plan.languages) >= MIN_LANGUAGES
    assert plan.degraded is False


def test_every_batch_holds_two_or_three_queries_within_the_word_bounds():
    plan = deterministic_plan(MIRA)

    assert plan.batches
    for batch in plan.batches:
        assert 2 <= len(batch.search_queries) <= 3
        lo, hi = word_bounds(batch.locale.language)
        for q in batch.search_queries:
            assert lo <= len(q.split()) <= hi, q


def test_non_english_queries_are_genuinely_not_english():
    plan = deterministic_plan(MIRA)
    by_language = {b.locale.language: b for b in plan.batches
                   if b.modality == "voice"}

    assert any(ord(ch) > 127 for ch in " ".join(by_language["ja"].search_queries))
    assert any(ord(ch) > 127 for ch in " ".join(by_language["hi"].search_queries))
    assert "voz" in " ".join(by_language["pt"].search_queries)
    assert "voz" in " ".join(by_language["es"].search_queries)
    for language, batch in by_language.items():
        if language == "en":
            continue
        assert "voice clone" not in " ".join(batch.search_queries).lower()


def test_the_name_and_its_aliases_are_crossed_with_modality_terms():
    plan = deterministic_plan(MIRA)
    text = " | ".join(q for b in plan.batches for q in b.search_queries)

    assert "Mira Vance" in text
    assert "M. Vance" in text or "Mira V." in text


def test_plan_stays_inside_the_candidate_budget():
    plan = deterministic_plan(MIRA)

    assert len(plan.batches) <= MAX_BATCHES
    assert len(plan.batches) * plan.results_per_batch <= CANDIDATE_BUDGET


def test_truncating_at_the_cap_costs_a_modality_not_a_language():
    plan = deterministic_plan(MIRA, max_batches=5)

    assert len(plan.batches) == 5
    assert len(plan.languages) == 5          # every locale kept its first modality


def test_grant_territories_earn_their_own_locale():
    """Permission in one territory and shipment in another is the thing we hunt."""
    plan = deterministic_plan(MIRA, consents=[GRANT_PT])

    assert Locale(language="pt", region="PT") in plan.locales


def test_a_grant_territory_we_cannot_search_in_is_skipped_not_faked():
    """An English query aimed at a French market finds nothing and looks like
    coverage — worse than an admitted gap."""
    grant_fr = Consent(id="c2", performer_id="perf_mira", licensee="X",
                       territories=["FR"])

    plan = deterministic_plan(MIRA, consents=[grant_fr])

    assert all(loc.region != "FR" for loc in plan.locales)


def test_a_long_name_is_shortened_rather_than_overflowing_the_word_budget():
    long_name = Performer(id="p2", name="Maria Da Silva Vance Ferreira")

    plan = deterministic_plan(long_name)

    for batch in plan.batches:
        lo, hi = word_bounds(batch.locale.language)
        for q in batch.search_queries:
            assert lo <= len(q.split()) <= hi, q


# --------------------------------------------------------------- validation

def test_validator_accepts_the_deterministic_plan():
    validate_plan(deterministic_plan(MIRA))       # does not raise


def test_validator_rejects_a_single_query_batch():
    plan = SearchPlan(performer_id="p", batches=(
        SearchBatch("o", ("Mira Vance AI voice clone",),
                    Locale("en", "US"), "voice"),))

    with pytest.raises(ValidationError, match="2-3"):
        validate_plan(plan)


def test_validator_rejects_a_sentence_as_a_query():
    good = deterministic_plan(MIRA)
    sentence = SearchBatch(
        "o", ("where can I buy an AI generated voice of Mira Vance today",
              "Mira Vance AI voice clone"), Locale("en", "US"), "voice")
    plan = SearchPlan(performer_id="p", batches=(sentence, *good.batches[1:]))

    with pytest.raises(ValidationError, match="words"):
        validate_plan(plan)


def test_validator_rejects_english_text_wearing_a_locale_tag():
    plan = SearchPlan(performer_id="p", batches=tuple(
        SearchBatch("o", ("Mira Vance AI voice clone",
                          "Mira Vance synthetic voice"), locale, "voice")
        for locale in (Locale("en", "US"), Locale("pt", "BR"), Locale("es", "MX"),
                       Locale("ja", "JP"), Locale("hi", "IN"))))

    with pytest.raises(ValidationError, match="English"):
        validate_plan(plan)


def test_validator_enforces_the_language_floor():
    plan = SearchPlan(performer_id="p", batches=tuple(
        SearchBatch("o", ("Mira Vance AI voice clone",
                          "Mira Vance synthetic voice"),
                    Locale("en", region), "voice")
        for region in ("US", "GB", "CA", "AU", "IE")))

    with pytest.raises(ValidationError, match="languages"):
        validate_plan(plan)


def test_validator_rejects_a_plan_over_the_batch_cap():
    base = deterministic_plan(MIRA)
    plan = SearchPlan(performer_id="p",
                      batches=base.batches + base.batches)

    with pytest.raises(ValidationError, match="batches"):
        validate_plan(plan)


# ------------------------------------------------------------- model path

def test_model_written_plan_is_used_when_it_validates():
    model = FakeModel()
    planner = QueryPlanner(deps=deps(), generate=model, sleep=lambda _s: None)

    plan = planner.plan(MIRA)

    assert plan.degraded is False
    assert plan.model == planner.model
    assert len(plan.locales) == 5
    assert len(model.calls) == 1
    assert "clone de voz" in " ".join(plan.batches[1].search_queries)


def test_the_model_is_told_the_constraints_and_given_the_performer():
    model = FakeModel()
    planner = QueryPlanner(deps=deps(), generate=model, sleep=lambda _s: None)

    planner.plan(MIRA, consents=[GRANT_PT])

    instruction, payload = model.calls[0]
    assert "2 or 3 keyword queries" in instruction
    assert "3 to 6 words" in instruction
    assert f"at least {MIN_LOCALES} locales" in instruction
    assert "AI音声クローン" in instruction          # vocabulary, in-language
    sent = json.loads(payload)
    assert sent["performer"]["name"] == "Mira Vance"
    assert sent["performer"]["aliases"] == ["M. Vance", "Mira V."]
    assert sent["existing_grants"][0]["licensee"] == "Aurora Studios"


def test_a_bad_shape_is_repaired_once_with_the_validator_s_own_words():
    bad = json.dumps({"batches": [
        {"objective": "o", "search_queries": ["Mira Vance AI voice clone"],
         "language": "en", "region": "US", "modality": "voice"}]})
    model = FakeModel(bad, model_plan())
    planner = QueryPlanner(deps=deps(), generate=model, sleep=lambda _s: None)

    result = planner.plan_detailed(MIRA)

    assert result.ok is True
    assert result.repairs == 1
    assert len(model.calls) == 2
    assert "rejected" in model.calls[1][1]
    assert "2-3" in model.calls[1][1]      # the hint is the validator's message


def test_a_model_that_keeps_failing_degrades_to_the_deterministic_plan():
    bad = json.dumps({"batches": []})
    model = FakeModel(bad, bad, bad)
    planner = QueryPlanner(deps=deps(), generate=model, sleep=lambda _s: None)

    plan = planner.plan(MIRA)

    assert plan.degraded is True
    assert plan.reason and "empty" in plan.reason
    # The sweep still runs, and still meets FR-2.2.
    validate_plan(plan)
    assert len(plan.languages) >= MIN_LANGUAGES


def test_prose_instead_of_json_degrades_rather_than_crashing():
    model = FakeModel("Sure! Here are some search ideas for Mira Vance:")
    planner = QueryPlanner(deps=deps(), generate=model, sleep=lambda _s: None)

    plan = planner.plan(MIRA)

    assert plan.degraded is True
    validate_plan(plan)


def test_a_model_outage_degrades_and_does_not_raise():
    def boom(_instruction: str, _payload: str) -> str:
        raise RuntimeError("503 model unavailable")

    planner = QueryPlanner(deps=deps(), generate=boom, sleep=lambda _s: None)

    result = planner.plan_detailed(MIRA)
    plan = planner.plan(MIRA)

    assert result.ok is False
    assert result.fail_state is FailState.DEGRADED
    assert plan.degraded is True


def test_a_fenced_json_block_is_still_parsed():
    fenced = "```json\n" + model_plan() + "\n```"
    planner = QueryPlanner(deps=deps(), generate=FakeModel(fenced),
                           sleep=lambda _s: None)

    plan = planner.plan(MIRA)

    assert plan.degraded is False
    assert len(plan.batches) == 5


# ------------------------------------------------------------------- cache

def test_second_plan_for_the_same_performer_comes_from_cache():
    model = FakeModel()
    planner = QueryPlanner(deps=deps(cache=MemoryCache()), generate=model,
                           sleep=lambda _s: None)

    planner.plan(MIRA)
    second = planner.plan_detailed(MIRA)

    assert len(model.calls) == 1
    assert second.from_cache is True


def test_prompt_version_is_part_of_the_cache_key():
    """Hard rule 8: without it you serve yesterday's prompt forever."""
    cache = MemoryCache()
    model = FakeModel()
    QueryPlanner(deps=deps(cache=cache, prompt_version="v1"), generate=model,
                 sleep=lambda _s: None).plan(MIRA)
    QueryPlanner(deps=deps(cache=cache, prompt_version="v2"), generate=model,
                 sleep=lambda _s: None).plan(MIRA)

    assert len(model.calls) == 2
    assert len(cache.entries) == 2
    assert all("v1" in k or "v2" in k for k in cache.entries)


# ------------------------------------------------------------------- audit

def test_audit_row_records_the_plan_shape_and_provenance():
    audit = MemoryAudit()
    planner = QueryPlanner(deps=deps(audit=audit), generate=FakeModel(),
                           sleep=lambda _s: None)

    planner.plan(MIRA)

    row = next(e for e in audit.events if e.get("event") == "plan")
    assert row["subject_id"] == "perf_mira"
    assert row["batches"] == 5
    assert len(row["locales"]) == 5
    assert row["temperature"] == 0.0
    assert row["from_cache"] is False
    assert row["cache_age_s"] is None
    assert row["prompt_version"] == "v1"


# ------------------------------------------------- hands off to parallel_search

def test_each_batch_is_directly_callable_as_a_parallel_search(caplog):
    """The two work units have to agree on the shape, so assert it rather than
    hoping: WU-05's shape warnings must stay silent for a WU-06 plan."""
    from consentinel.tools.parallel_search import _warn_on_query_shape

    plan = deterministic_plan(MIRA)

    calls = list(iter_search_calls(plan))
    assert len(calls) == len(plan.batches)
    assert calls[0]["max_results"] == plan.results_per_batch
    assert isinstance(calls[0]["locale"], Locale)
    with caplog.at_level(logging.WARNING, logger="consentinel.parallel_search"):
        for call in calls:
            _warn_on_query_shape(call["search_queries"])
    assert [r.getMessage() for r in caplog.records] == []


# --------------------------------------------------------------- ADK wiring

def test_the_agent_is_a_schema_constrained_llm_agent_at_temperature_zero():
    """Built, not run: no model call, no credentials, no network."""
    from consentinel.agents.query_planner import PlanOut, build_agent

    agent = build_agent(model="gemini-2.5-flash")

    assert agent.output_schema is PlanOut
    assert agent.generate_content_config.temperature == 0.0
    assert not agent.tools                      # hard rule 3: no tools
    assert agent.disallow_transfer_to_parent is True
    assert agent.disallow_transfer_to_peers is True


def test_policy_declares_no_tools_and_one_repair():
    planner = QueryPlanner(deps=deps(), generate=FakeModel())

    assert planner.policy.tools == ()
    assert planner.policy.temperature == 0.0
    assert planner.policy.max_repairs == 1
    assert planner.policy.fail_state is FailState.DEGRADED


def test_parse_plan_rejects_an_empty_answer():
    with pytest.raises(ValidationError, match="nothing"):
        parse_plan("", "p", None)


def test_build_instruction_lists_vocabulary_for_every_planned_locale():
    instruction = build_instruction()

    for term in ("AI voice clone", "clone de voz IA", "clon de voz IA",
                 "AI音声クローン", "एआई आवाज क्लोन"):
        assert term in instruction


def test_payload_says_grants_are_context_not_an_exclusion_list():
    """Hard rule 4: the planner must not pre-decide what is authorised."""
    sent = json.loads(build_payload(MIRA, [GRANT_PT]))

    assert "context only" in sent["note"]
    assert sent["existing_grants"][0]["territories"] == ["PT"]
