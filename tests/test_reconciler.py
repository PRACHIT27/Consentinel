"""WU-13 — every branch of the rule engine.

The highest return per hour of testing in the project: the whole product rests
on this component, and it is deterministic, so it is cheap to test properly.

The required cases from the ticket are all here, the territory-scoping one
included: a grant covering US and CA against an offering targeting US *and* BR
must be unauthorised, and the reasoning must name BR.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from consentinel.agents.reconciler import (
    ACTOR_NOT_LICENSEE,
    PERFORMER_NOT_IDENTIFIED,
    ACTOR_UNKNOWN,
    CONFIDENCE_BELOW_THRESHOLD,
    COVERED_BY_GRANT,
    GRANT_EXPIRED,
    GRANT_NOT_YET_EFFECTIVE,
    MODALITY_NOT_PERMITTED,
    MODALITY_UNKNOWN,
    NO_CITATION,
    NO_GRANT,
    TERRITORIES_UNKNOWN,
    TERRITORY_OUTSIDE_GRANT,
    MissingCitation,
    Observation,
    Reconciler,
    VerdictResult,
    evaluate,
)
from consentinel.harness import HarnessDeps, MemoryAudit
from consentinel.store.base import Consent, PermittedUse, Verdict
from consentinel.tools.contracts import TriageExtraction

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
QUOTE = "Compre o clone de voz de Mira Vance por R$ 49,90."


def grant(**kw: Any) -> Consent:
    base = dict(
        id="c_aurora_voice",
        performer_id="perf_mira",
        licensee="Aurora Studios",
        permitted_uses=[PermittedUse.VOICE_SYNTH],
        territories=["US", "CA"],
        valid_from=NOW - timedelta(days=30),
        valid_to=NOW + timedelta(days=300),
    )
    base.update(kw)
    return Consent(**base)


def observe(**kw: Any) -> Observation:
    base = dict(
        performer_id="perf_mira",
        # Stated explicitly: since the first live sweep, a verdict requires
        # that the page actually names the performer.
        depicts_named_person=True,
        modality="voice",
        target_territories=("US",),
        actor="Aurora Studios",
        confidence=0.9,
        evidence_quote=QUOTE,
    )
    base.update(kw)
    return Observation(**base)


# --------------------------------------------------------- the required cases

def test_no_grant_at_all_is_unauthorized():
    result = evaluate(observe(), [], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == NO_GRANT
    assert "no consent on file" in result.reason
    assert result.citation == QUOTE
    assert result.grants_considered == 0


def test_a_grant_for_someone_else_is_no_grant_at_all():
    other = grant(id="c_other", performer_id="perf_elena")

    result = evaluate(observe(), [other], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == NO_GRANT


def test_a_voice_grant_does_not_cover_a_face_use():
    result = evaluate(observe(modality="face"), [grant()], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == MODALITY_NOT_PERMITTED
    assert "voice_synth" in result.reason
    assert "face" in result.reason
    assert result.breached_consent_id == "c_aurora_voice"


def test_the_right_modality_in_the_wrong_territory_is_unauthorized():
    result = evaluate(observe(target_territories=("BR",)), [grant()], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == TERRITORY_OUTSIDE_GRANT
    assert result.territories_outside == ("BR",)


def test_an_expired_grant_is_unauthorized():
    result = evaluate(observe(),
                      [grant(valid_to=NOW - timedelta(days=1))], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == GRANT_EXPIRED
    assert "expired on 2026-09-08" in result.reason


def test_a_not_yet_effective_grant_is_unauthorized():
    result = evaluate(observe(),
                      [grant(valid_from=NOW + timedelta(days=10))], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == GRANT_NOT_YET_EFFECTIVE
    assert "does not take effect until 2026-09-19" in result.reason


def test_a_third_party_rather_than_the_licensee_is_unauthorized():
    result = evaluate(observe(actor="VozClone Studio"), [grant()], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == ACTOR_NOT_LICENSEE
    assert "VozClone Studio is offering this" in result.reason
    assert "Aurora Studios" in result.reason


def test_confidence_below_the_threshold_is_ambiguous():
    result = evaluate(observe(confidence=0.4), [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == CONFIDENCE_BELOW_THRESHOLD
    assert "0.40" in result.reason
    assert "a human decides" in result.reason


def test_a_fully_covered_use_is_authorized():
    result = evaluate(observe(), [grant()], NOW)

    assert result.verdict is Verdict.AUTHORIZED
    assert result.check == COVERED_BY_GRANT
    assert result.matched_consent_id == "c_aurora_voice"
    assert result.citation == QUOTE
    # Sorted, so the message is deterministic across runs.
    assert "voice_synth" in result.reason and "CA, US" in result.reason


# ------------------------------------------------------------ territory scoping

def test_a_grant_for_us_and_ca_against_an_offering_in_us_and_br():
    """The ticket's headline case. Partial coverage is not coverage, and the
    reasoning has to name the territory that broke it."""
    result = evaluate(observe(target_territories=("US", "BR")), [grant()], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == TERRITORY_OUTSIDE_GRANT
    assert result.territories_outside == ("BR",)
    assert "BR" in result.reason
    assert "CA, US" in result.reason          # sorted, hence CA first


def test_every_territory_outside_the_grant_is_named():
    result = evaluate(observe(target_territories=("US", "BR", "PT", "JP")),
                      [grant()], NOW)

    assert result.territories_outside == ("BR", "JP", "PT")
    for code in ("BR", "JP", "PT"):
        assert code in result.reason


def test_a_worldwide_grant_covers_any_territory():
    result = evaluate(observe(target_territories=("BR", "JP")),
                      [grant(territories=["WORLDWIDE"])], NOW)

    assert result.verdict is Verdict.AUTHORIZED


def test_territory_comparison_is_a_set_not_a_single_value():
    """FR-4.6. Order and duplication must not change the answer."""
    a = evaluate(observe(target_territories=("US", "CA")), [grant()], NOW)
    b = evaluate(observe(target_territories=("CA", "US", "US")), [grant()], NOW)

    assert a.verdict is b.verdict is Verdict.AUTHORIZED


# -------------------------------------------------------------- the citation

def test_a_decisive_verdict_without_a_quote_raises_when_asked_to_store_it():
    """FR-4.4: reject it, do not store it."""
    with pytest.raises(MissingCitation):
        VerdictResult(verdict=Verdict.UNAUTHORIZED, check=NO_GRANT,
                      reason="no grant", citation=None).assert_citable()

    with pytest.raises(MissingCitation):
        VerdictResult(verdict=Verdict.UNAUTHORIZED, check=NO_GRANT,
                      reason="no grant", citation="  ").to_finding_fields()


def test_strict_callers_get_the_exception_during_evaluation():
    with pytest.raises(MissingCitation):
        evaluate(observe(evidence_quote=None), [grant()], NOW,
                 strict_citation=True)


def test_by_default_an_uncitable_verdict_degrades_instead_of_raising():
    """One uncitable page should degrade itself, not the sweep (hard rule 10)."""
    result = evaluate(observe(evidence_quote=None), [], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == NO_CITATION
    assert "would have been" in result.reason
    assert "unauthorized" in result.reason


def test_an_ambiguous_verdict_needs_no_citation():
    """It asserts nothing, and a page supporting no quote is what it is for."""
    result = evaluate(observe(confidence=0.3, evidence_quote=None),
                      [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    result.assert_citable()          # does not raise
    assert result.to_finding_fields()["evidence_quote"] is None


def test_to_finding_fields_hands_over_the_three_columns_and_the_quote():
    fields = evaluate(observe(), [grant()], NOW).to_finding_fields()

    assert fields["verdict"] is Verdict.AUTHORIZED
    assert fields["matched_consent_id"] == "c_aurora_voice"
    assert fields["evidence_quote"] == QUOTE
    assert "authorised" in fields["reasoning"]


# ------------------------------------------------- several grants, one answer

def test_any_grant_that_covers_the_use_authorises_it():
    voice_us = grant(id="c_voice_us", territories=["US"])
    voice_br = grant(id="c_voice_br", licensee="Bossa Filmes",
                     territories=["BR"])

    result = evaluate(observe(target_territories=("BR",), actor="Bossa Filmes"),
                      [voice_us, voice_br], NOW)

    assert result.verdict is Verdict.AUTHORIZED
    assert result.matched_consent_id == "c_voice_br"
    assert result.grants_considered == 2


def test_the_closest_failing_grant_explains_the_verdict():
    """"No grant covers this" is useless to a human holding four contracts."""
    wrong_modality = grant(id="c_face", permitted_uses=[PermittedUse.FACE_REPLACE])
    right_modality_wrong_place = grant(id="c_voice", territories=["US"])

    result = evaluate(observe(target_territories=("BR",)),
                      [wrong_modality, right_modality_wrong_place], NOW)

    assert result.check == TERRITORY_OUTSIDE_GRANT      # got further than modality
    assert result.breached_consent_id == "c_voice"


def test_full_replica_covers_every_modality():
    replica = grant(id="c_replica", permitted_uses=[PermittedUse.FULL_REPLICA])

    for modality in ("voice", "face", "performance"):
        result = evaluate(observe(modality=modality), [replica], NOW)
        assert result.verdict is Verdict.AUTHORIZED, modality


def test_archival_reuse_does_not_authorise_a_synthetic_copy():
    """Permission to reuse existing footage is not permission to synthesise a
    new performance — conflating them would authorise the exact thing this
    product exists to catch."""
    archival = grant(id="c_archival",
                     permitted_uses=[PermittedUse.ARCHIVAL_REUSE])

    result = evaluate(observe(), [archival], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == MODALITY_NOT_PERMITTED


# --------------------------------------------------- the gaps, all toward doubt

def test_an_unknown_actor_inside_a_grant_is_ambiguous_not_authorized():
    """Not knowing who is selling is not the same as knowing it is a third
    party — and it is certainly not permission."""
    result = evaluate(observe(actor=None), [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == ACTOR_UNKNOWN
    assert "not knowing is not the same" in result.reason


def test_an_unknown_territory_against_a_limited_grant_is_ambiguous():
    """An empty set is vacuously a subset of anything, which would let "we
    could not tell" pass as authorised."""
    result = evaluate(observe(target_territories=()), [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == TERRITORIES_UNKNOWN


def test_an_unknown_territory_against_a_worldwide_grant_still_resolves():
    """Scope cannot be breached when the grant has no scope limit."""
    result = evaluate(observe(target_territories=()),
                      [grant(territories=["WORLDWIDE"])], NOW)

    assert result.verdict is Verdict.AUTHORIZED


def test_an_unknown_modality_is_ambiguous():
    result = evaluate(observe(modality=None), [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == MODALITY_UNKNOWN


def test_low_confidence_cannot_support_unauthorized_either():
    """A weak reading cannot assert an infringement any more than it can
    assert permission (DESIGN §3 L5)."""
    result = evaluate(observe(confidence=0.2, target_territories=("BR",)),
                      [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == CONFIDENCE_BELOW_THRESHOLD
    assert "closest grant" in result.reason
    assert result.breached_consent_id == "c_aurora_voice"


def test_the_threshold_boundary_is_inclusive():
    assert evaluate(observe(confidence=0.6), [grant()], NOW).verdict \
        is Verdict.AUTHORIZED
    assert evaluate(observe(confidence=0.5999), [grant()], NOW).verdict \
        is Verdict.AMBIGUOUS


# ------------------------------------------------------------- party matching

@pytest.mark.parametrize("actor", [
    "Aurora Studios", "aurora studios", "Aurora Studios, LLC",
    "AURORA STUDIOS LLC", "Aurora  Studios", "Aurora",
])
def test_the_licensee_is_recognised_through_punctuation_and_suffixes(actor):
    """Anything less forgiving reports the licensee as a third party over a
    comma."""
    result = evaluate(observe(actor=actor), [grant()], NOW)

    assert result.verdict is Verdict.AUTHORIZED, actor


@pytest.mark.parametrize("actor", ["Aurora Films", "Borealis Studios",
                                   "VozClone", "Aurora Studios Brasil"])
def test_a_different_company_is_not_the_licensee(actor):
    result = evaluate(observe(actor=actor), [grant()], NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == ACTOR_NOT_LICENSEE


# ------------------------------------------------------------------ the dates

def test_an_open_ended_grant_never_expires():
    result = evaluate(observe(), [grant(valid_from=None, valid_to=None)], NOW)

    assert result.verdict is Verdict.AUTHORIZED


def test_a_naive_datetime_is_read_as_utc_rather_than_crashing():
    naive = grant(valid_from=datetime(2026, 1, 1), valid_to=datetime(2027, 1, 1))

    result = evaluate(observe(), [naive], NOW)

    assert result.verdict is Verdict.AUTHORIZED


def test_the_verdict_depends_on_when_you_ask():
    expiring = grant(valid_to=NOW + timedelta(days=1))

    assert evaluate(observe(), [expiring], NOW).verdict is Verdict.AUTHORIZED
    assert evaluate(observe(), [expiring], NOW + timedelta(days=2)).verdict \
        is Verdict.UNAUTHORIZED


# --------------------------------------------- no model, no page text, no cache

def test_the_module_imports_no_model_client():
    """WU-12: if you find yourself importing a Gemini client here, stop.

    Parsed rather than grepped. The first version of this test searched the
    source text and failed on a *comment* explaining that this module is not an
    `LlmAgent` — a test that cannot tell code from prose about code.
    """
    import ast

    source = Path("consentinel/agents/reconciler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    forbidden = ("google.genai", "google.adk", "google.cloud.aiplatform",
                 "vertexai", "openai", "anthropic")
    assert not [m for m in imported if m.startswith(forbidden)], sorted(imported)

    called = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "generate_content" not in called
    assert "generate" not in called


def test_the_observation_has_no_way_to_carry_page_text():
    """FR-4.3. The only page-derived string here is a validated citation."""
    fields = set(Observation.__dataclass_fields__)

    assert "page_text" not in fields
    assert "text" not in fields
    assert "snapshot" not in fields
    assert fields == {"performer_id", "depicts_named_person", "modality",
                      "target_territories", "actor", "confidence",
                      "evidence_quote"}


def test_nothing_in_this_module_writes_to_a_cache():
    """Hard rule 7: grants expire and get revoked, so a stored verdict would
    keep asserting yesterday's answer."""
    cache_calls: list[str] = []

    class LoudCache:
        def get(self, key):
            cache_calls.append(f"get:{key}")
            return None

        def put(self, key, value, ttl):
            cache_calls.append(f"put:{key}")

    r = Reconciler(deps=HarnessDeps(cache=LoudCache(), audit=MemoryAudit()))
    r.reconcile(observe(), [grant()], NOW)

    assert cache_calls == []


def test_the_same_inputs_always_give_the_same_verdict():
    first = evaluate(observe(), [grant()], NOW)
    second = evaluate(observe(), [grant()], NOW)

    assert first == second


# ------------------------------------------------------------------ the trail

def test_every_verdict_writes_an_audit_row_naming_its_check():
    audit = MemoryAudit()
    r = Reconciler(deps=HarnessDeps(audit=audit))

    r.reconcile(observe(target_territories=("US", "BR")), [grant()], NOW)

    row = next(e for e in audit.events if e.get("event") == "verdict")
    assert row["verdict"] == "unauthorized"
    assert row["check"] == TERRITORY_OUTSIDE_GRANT
    assert row["territories_outside"] == ["BR"]
    assert row["breached_consent_id"] == "c_aurora_voice"
    assert row["has_citation"] is True
    assert row["model"] is None            # there is no model in this module
    assert row["subject_id"] == "perf_mira"


def test_the_audit_row_carries_no_quote_text_only_that_one_exists():
    audit = MemoryAudit()
    Reconciler(deps=HarnessDeps(audit=audit)).reconcile(observe(), [grant()], NOW)

    row = next(e for e in audit.events if e.get("event") == "verdict")
    assert QUOTE not in str(row)
    assert row["has_citation"] is True


def test_the_log_line_names_the_verdict_and_the_check(caplog):
    r = Reconciler(deps=HarnessDeps(audit=MemoryAudit()))

    with caplog.at_level(logging.INFO, logger="consentinel.reconciler"):
        r.reconcile(observe(target_territories=("BR",)), [grant()], NOW)

    line = next(m.getMessage() for m in caplog.records
                if m.getMessage().startswith("Reconciler "))
    assert "verdict=unauthorized" in line
    assert f"check={TERRITORY_OUTSIDE_GRANT}" in line
    assert "outside=BR" in line


# ------------------------------------------------------- from an extraction

def test_an_extraction_becomes_an_observation_without_its_page():
    extraction = TriageExtraction(
        depicts_named_person=True, person_name="Mira Vance",
        is_synthetic_claim=True, modality="voice", is_commercial=True,
        target_territories=["br", " pt "], evidence_quote=QUOTE,
        confidence=0.88)

    observation = Observation.from_extraction(extraction, "perf_mira",
                                              actor="VozClone Studio")

    assert observation.target_territories == ("BR", "PT")
    assert observation.evidence_quote == QUOTE
    assert observation.actor == "VozClone Studio"


def test_the_enforcement_path_runs_end_to_end_from_an_extraction():
    extraction = TriageExtraction(
        depicts_named_person=True, person_name="Mira Vance",
        is_synthetic_claim=True, modality="voice", is_commercial=True,
        target_territories=["BR"], evidence_quote=QUOTE, confidence=0.93)
    r = Reconciler(deps=HarnessDeps(audit=MemoryAudit()))

    result = r.for_extraction(extraction, "perf_mira", [grant()],
                              actor="VozClone Studio", as_of=NOW)

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.check == TERRITORY_OUTSIDE_GRANT
    assert result.territories_outside == ("BR",)
    assert result.to_finding_fields()["evidence_quote"] == QUOTE

def test_a_page_that_does_not_name_the_performer_gets_no_verdict():
    """The gap the first live sweep exposed: a generic voice-cloning product
    page names nobody, so the name check passes vacuously and the engine would
    otherwise answer "is this covered by her grants?" about a page that has
    nothing to do with her."""
    result = evaluate(observe(depicts_named_person=False), [grant()], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == PERFORMER_NOT_IDENTIFIED
    assert "does not name or depict" in result.reason


def test_identity_is_checked_before_anything_else():
    """Even with no grant on file at all, an unrelated page is ambiguous rather
    than unauthorized — `unauthorized` is wrong in the direction that accuses a
    stranger."""
    result = evaluate(observe(depicts_named_person=False), [], NOW)

    assert result.verdict is Verdict.AMBIGUOUS
    assert result.check == PERFORMER_NOT_IDENTIFIED


def test_an_extraction_carries_its_identity_claim_into_the_observation():
    extraction = TriageExtraction(
        depicts_named_person=False, person_name=None, is_synthetic_claim=True,
        modality="voice", is_commercial=True, target_territories=["BR"],
        evidence_quote=QUOTE, confidence=0.95)

    observation = Observation.from_extraction(extraction, "perf_mira")

    assert observation.depicts_named_person is False
