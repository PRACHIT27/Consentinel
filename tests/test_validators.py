"""WU-10 acceptance.

Done when: an extraction carrying a fabricated quote is rejected. That is the
first test below, and it is the highest-value guardrail in the system — one
string comparison that makes quote hallucination structurally impossible
(DESIGN §3 L3, hard rule 5).
"""

from __future__ import annotations

import pytest

from consentinel.agents.validators import (
    MIN_QUOTE_CHARS,
    REVIEW_THRESHOLD,
    check_person_name_matches,
    check_quote_is_verbatim,
    failures,
    is_reviewable,
    normalise_whitespace,
    quoted_span,
    validate_extraction,
)
from consentinel.harness import ValidationError
from consentinel.store.base import Performer
from consentinel.tools.contracts import TriageExtraction

MIRA = Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])

PAGE = (
    "Voz IA Mira Vance\n"
    "Compre o clone de voz de Mira Vance por R$ 49,90.\n"
    "Entrega imediata para todo o Brasil. Licença comercial incluída."
)


def extraction(**kw) -> TriageExtraction:
    base = dict(
        depicts_named_person=True,
        person_name="Mira Vance",
        is_synthetic_claim=True,
        modality="voice",
        is_commercial=True,
        target_territories=["BR"],
        evidence_quote="Compre o clone de voz de Mira Vance por R$ 49,90.",
        confidence=0.9,
    )
    base.update(kw)
    return TriageExtraction(**base)


# ------------------------------------------------------- the acceptance test

def test_a_fabricated_quote_is_rejected():
    """The model wrote a plausible sentence that is not on the page."""
    fake = extraction(
        evidence_quote="This listing is fully licensed by the rights holder.")

    with pytest.raises(ValidationError, match="verbatim"):
        validate_extraction(fake, PAGE, MIRA)


def test_a_quote_that_is_on_the_page_passes():
    validate_extraction(extraction(), PAGE, MIRA)      # does not raise


def test_a_quote_altered_in_the_middle_is_rejected():
    """Half-real is the dangerous case: it survives a human skim."""
    tampered = extraction(
        evidence_quote="Compre o clone de voz de Mira Vance por R$ 9,90.")

    with pytest.raises(ValidationError, match="verbatim"):
        validate_extraction(tampered, PAGE, MIRA)


def test_whitespace_differences_are_forgiven_and_nothing_else_is():
    """Normalise whitespace before comparing, nothing more (WU-10)."""
    respaced = extraction(
        evidence_quote="Compre  o clone\nde voz de Mira Vance por R$ 49,90.")
    validate_extraction(respaced, PAGE, MIRA)

    lowercased = extraction(
        evidence_quote="compre o clone de voz de mira vance por r$ 49,90.")
    with pytest.raises(ValidationError):
        validate_extraction(lowercased, PAGE, MIRA)


def test_a_non_breaking_space_still_matches():
    """A rendering difference is not a fabrication.

    The page below holds a real NO-BREAK SPACE where the quote has an
    ordinary one, which is exactly what NFKC normalisation is for.
    """
    page = "Compre o clone de voz de Mira Vance hoje."  # noqa: RUF001 - a real NBSP, that is the point
    check_quote_is_verbatim(
        extraction(evidence_quote="Compre o clone de voz de Mira Vance hoje."), page)


def test_a_null_quote_is_allowed():
    """FR-3: "a quote or an explicit null". Plenty of pages prove nothing."""
    validate_extraction(
        extraction(evidence_quote=None, is_synthetic_claim=False), PAGE, MIRA)


def test_a_quote_too_short_to_prove_anything_is_rejected():
    with pytest.raises(ValidationError, match="too short"):
        check_quote_is_verbatim(extraction(evidence_quote="voz"), PAGE)
    assert MIN_QUOTE_CHARS >= 8


# ------------------------------------------------------------- person name

def test_the_performer_name_matches_in_the_forms_a_listing_uses():
    for name in ("Mira Vance", "mira vance", "MIRA VANCE", "M. Vance",
                 "Mira  Vance", "Mira Vancé"):
        check_person_name_matches(extraction(person_name=name), MIRA)


def test_a_different_person_voids_the_extraction():
    """The model drifted, and everything downstream would be about the wrong
    human."""
    with pytest.raises(ValidationError, match="different person"):
        check_person_name_matches(extraction(person_name="Elena Marsh"), MIRA)


def test_a_different_surname_never_matches_on_the_first_initial_alone():
    with pytest.raises(ValidationError):
        check_person_name_matches(extraction(person_name="Mira Vasquez"), MIRA)


def test_depicting_a_named_person_requires_naming_them():
    with pytest.raises(ValidationError, match="person_name is empty"):
        check_person_name_matches(
            extraction(person_name="", is_synthetic_claim=False), MIRA)


def test_a_name_is_not_checked_when_no_person_is_depicted():
    check_person_name_matches(
        extraction(depicts_named_person=False, person_name=None), MIRA)


def test_the_name_check_is_skipped_when_there_is_no_performer_to_compare_to():
    check_person_name_matches(extraction(person_name="Anyone At All"), None)


# ------------------------------------------------------------- other fields

@pytest.mark.parametrize("territories", [["ZZZ"], ["br"], ["Brazil"], [""], ["B"]])
def test_invalid_territory_codes_are_rejected(territories):
    """Territory drives the verdict — consent is territory-scoped — so a
    made-up code silently widens or narrows a grant."""
    with pytest.raises(ValidationError, match="3166"):
        validate_extraction(extraction(target_territories=territories), PAGE, MIRA)


def test_worldwide_and_alpha_2_codes_are_accepted():
    validate_extraction(extraction(target_territories=["BR", "US", "WORLDWIDE"]),
                        PAGE, MIRA)


@pytest.mark.parametrize("value", [-0.1, 1.5, None, "high"])
def test_confidence_outside_the_range_is_rejected(value):
    with pytest.raises(ValidationError, match="confidence"):
        validate_extraction(extraction(confidence=value), PAGE, MIRA)


@pytest.mark.parametrize("modality", ["audio", "video", "vocal", "Voice!"])
def test_a_modality_outside_the_enum_is_rejected(modality):
    with pytest.raises(ValidationError, match="modality"):
        validate_extraction(extraction(modality=modality), PAGE, MIRA)


def test_the_enum_values_and_a_null_modality_are_accepted():
    for modality in ("voice", "face", "performance", None):
        validate_extraction(extraction(modality=modality), PAGE, MIRA)


# ---------------------------------------------------------- self-consistency

def test_a_synthetic_claim_with_nothing_to_quote_is_rejected():
    """This is the shape an injected instruction takes once the schema has
    removed every other channel: the page told the model what to answer, and
    there is no sentence to point at."""
    with pytest.raises(ValidationError, match="quotable"):
        validate_extraction(
            extraction(is_synthetic_claim=True, evidence_quote=None), PAGE, MIRA)


def test_naming_a_person_while_denying_one_is_depicted_is_rejected():
    with pytest.raises(ValidationError, match="depicts_named_person is false"):
        validate_extraction(
            extraction(depicts_named_person=False, person_name="Mira Vance",
                       is_synthetic_claim=False, evidence_quote=None), PAGE, MIRA)


# ------------------------------------------------------------------ helpers

def test_failures_reports_everything_wrong_at_once():
    bad = extraction(confidence=2.0, modality="audio",
                     target_territories=["Brazil"],
                     evidence_quote="not on the page at all, promise")

    found = failures(bad, PAGE, MIRA)

    assert len(found) == 4
    assert any("confidence" in f for f in found)
    assert any("modality" in f for f in found)
    assert any("3166" in f for f in found)
    assert any("verbatim" in f for f in found)


def test_the_review_threshold_sends_low_confidence_to_a_human():
    assert REVIEW_THRESHOLD == 0.6
    assert is_reviewable(extraction(confidence=0.61)) is True
    assert is_reviewable(extraction(confidence=0.59)) is False


def test_quoted_span_locates_the_sentence_for_the_decision_trail():
    span = quoted_span(extraction(), PAGE)

    assert span is not None
    start, end = span
    assert normalise_whitespace(PAGE)[start:end] == \
           "Compre o clone de voz de Mira Vance por R$ 49,90."


def test_quoted_span_is_none_for_a_quote_that_is_not_there():
    assert quoted_span(extraction(evidence_quote="invented sentence"), PAGE) is None
    assert quoted_span(extraction(evidence_quote=None), PAGE) is None


def test_the_cheapest_check_fails_first_so_the_repair_hint_is_actionable():
    """A model that gets two things wrong should be told about the one it can
    fix in a single edit."""
    both = extraction(confidence=9.0, evidence_quote="fabricated entirely")

    with pytest.raises(ValidationError, match="confidence"):
        validate_extraction(both, PAGE, MIRA)
