"""WU-03 — reading a contract into a permission slip.

Most of this runs offline with a stand-in for the model, because the rules being
tested are ours, not Gemini's. One test at the end makes a real call and asserts
the thing the demo depends on: that a withheld right is not granted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from consentinel.agents.consent_ingest import (
    ConsentDraft,
    _check_citations_are_real,
    _check_vocabulary,
    cache_key,
    extract_consent,
    read_pages,
    to_consent,
)
from consentinel.harness.errors import ValidationError
from consentinel.harness.policy import FailState
from consentinel.store.base import PermittedUse

PDF = Path(__file__).resolve().parent.parent / "fixtures" / "docs" / "mira_vance_halcyon_agreement.pdf"
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")

needs_pdf = pytest.mark.skipif(not PDF.exists(), reason="run tools/make_contract_pdf.py")
needs_gcp = pytest.mark.skipif(not PROJECT, reason="GOOGLE_CLOUD_PROJECT not set")


@pytest.fixture(scope="module")
def pages():
    return read_pages(PDF)


def fake_model(payload):
    """A stand-in for Gemini that returns whatever we hand it."""
    return lambda instruction, document, schema: payload


# ------------------------------------------------------------------ reading


@needs_pdf
def test_reads_every_page(pages):
    assert len(pages) == 6
    assert "synthetic voice performances" in " ".join(pages)


@needs_pdf
def test_cache_key_follows_the_file_not_the_name():
    """Content-addressed: the same bytes always give the same key, so
    re-reading a contract during development costs nothing."""
    assert cache_key(PDF) == cache_key(PDF)
    assert cache_key(PDF).startswith("consent_ingest:")


# --------------------------------------------------------------- the quote rule


@needs_pdf
def test_a_made_up_quote_is_rejected(pages):
    """The guardrail that matters. One string comparison, and an invented
    citation becomes impossible."""
    validate = _check_citations_are_real(pages)
    with pytest.raises(ValidationError) as e:
        validate({"citations": [{"field": "licensee", "quote": "Producer may do whatever it likes.", "page": 4}]})
    assert "not on any page" in str(e.value)


@needs_pdf
def test_a_real_quote_on_the_wrong_page_is_rejected(pages):
    """A citation that points at the wrong page is worse than none — someone
    checking it would look at page 1 and conclude we invented it."""
    validate = _check_citations_are_real(pages)
    with pytest.raises(ValidationError) as e:
        validate({"citations": [{
            "field": "permitted_uses",
            "quote": "Producer may generate synthetic voice performances of the Artist solely for the Picture, in the United States and Canada.",
            "page": 1,
        }]})
    assert "page 4" in str(e.value)


@needs_pdf
def test_line_breaks_in_the_pdf_do_not_break_a_good_quote(pages):
    """PDF text reflows, so a sentence can arrive with a newline in the middle.
    Whitespace is the only tolerance allowed."""
    validate = _check_citations_are_real(pages)
    validate({"citations": [{
        "field": "permitted_uses",
        "quote": "Producer may generate synthetic\n   voice performances of the Artist solely for the Picture,\nin the United States and Canada.",
        "page": 4,
    }]})


@needs_pdf
def test_curly_quotes_in_a_real_copy_still_match(pages):
    """Gemini re-typesets punctuation when it copies a sentence - we watched it
    return curly quotes for a page that renders them straight. That is how the
    characters are drawn, not what they say, so it must not be treated as a
    fabrication."""
    validate = _check_citations_are_real(pages)
    validate({"citations": [{
        "field": "territories",
        "quote": "“Territory” means, except where this Agreement provides otherwise, the United States and Canada.",
        "page": 1,
    }]})


@needs_pdf
def test_a_page_that_does_not_exist_is_rejected(pages):
    validate = _check_citations_are_real(pages)
    with pytest.raises(ValidationError) as e:
        validate({"citations": [{"field": "licensee", "quote": "anything", "page": 99}]})
    assert "6-page" in str(e.value)


# ------------------------------------------------------------- the vocabulary


@pytest.mark.parametrize("payload,expect", [
    ({"permitted_uses": ["make_a_sequel"]}, "not one of"),
    ({"territories": ["United States"]}, "two-letter country code"),
    ({"valid_from": "1 Jan 2026"}, "YYYY-MM-DD"),
])
def test_invented_values_are_rejected(payload, expect):
    with pytest.raises(ValidationError) as e:
        _check_vocabulary(payload)
    assert expect in str(e.value)


def test_the_allowed_vocabulary_passes():
    _check_vocabulary({
        "permitted_uses": ["voice_synth", "archival_reuse"],
        "territories": ["US", "CA"],
        "valid_from": "2026-01-01",
        "valid_to": "2028-12-31",
    })
    _check_vocabulary({"territories": ["WORLDWIDE"]})


# ---------------------------------------------------------------- the draft


@needs_pdf
def test_a_field_with_no_citation_is_dropped():
    """An uncited field would be a permission record nobody can check, which is
    the exact thing this step exists to prevent."""
    quote = "This Agreement is made as of 1 January 2026 between Halcyon Pictures LLC"
    r = extract_consent(PDF, generate=fake_model({
        "performer_name": "Mira Vance",
        "licensee": "Halcyon Pictures LLC",
        "permitted_uses": ["voice_synth"],
        "territories": ["US"],
        "citations": [{"field": "licensee", "quote": quote, "page": 1}],
    }))
    assert r.ok
    d = r.value
    assert d.licensee == "Halcyon Pictures LLC"       # cited, kept
    assert d.performer_name == ""                     # uncited, dropped
    assert d.permitted_uses == []                     # uncited, dropped
    dropped = {x["field"] for x in d.dropped}
    assert {"performer_name", "permitted_uses", "territories"} <= dropped


@needs_pdf
def test_nothing_is_written_to_the_database():
    """This step returns a draft. A person confirms it before anything is
    stored, because a wrong permission slip poisons every later answer."""
    r = extract_consent(PDF, generate=fake_model({
        "performer_name": "", "licensee": "", "permitted_uses": [],
        "territories": [], "citations": [],
    }))
    assert r.ok
    assert isinstance(r.value, ConsentDraft)


def test_a_pdf_with_no_text_fails_toward_doubt(tmp_path):
    """A scanned contract needs OCR. It must not come back as an empty but
    successful permission slip."""
    from reportlab.pdfgen import canvas

    blank = tmp_path / "scan.pdf"
    canvas.Canvas(str(blank)).save()

    r = extract_consent(blank, generate=fake_model({}))
    assert not r.ok
    assert r.fail_state is FailState.UNVERIFIED
    assert "OCR" in r.reason


@needs_pdf
def test_a_confirmed_draft_becomes_a_storable_record():
    draft = ConsentDraft(
        licensee="Halcyon Pictures LLC",
        permitted_uses=["voice_synth", "archival_reuse"],
        territories=["US", "CA"],
        valid_from="2026-01-01",
        valid_to="2028-12-31",
        compensation_trigger="per-title fee",
        citations=[{"field": "licensee", "quote": "q", "page": 1}],
        source_doc_ref=str(PDF),
    )
    c = to_consent(draft, consent_id="c1", performer_id="p1")
    assert c.permitted_uses == [PermittedUse.VOICE_SYNTH, PermittedUse.ARCHIVAL_REUSE]
    assert c.territories == ["US", "CA"]
    assert c.valid_from.year == 2026 and c.valid_to.year == 2028
    assert c.clause_citations == [{"quote": "q", "page": 1}]


# ------------------------------------------------------------- the real thing


@needs_pdf
@needs_gcp
def test_gemini_does_not_grant_a_right_the_contract_withholds():
    """The one that matters for the demo.

    Page 4 grants a synthetic voice. Page 5 says a synthetic visual likeness is
    NOT granted. If the model treats that sentence as a grant, the blocked clip
    in the clearance demo turns green and the whole point collapses.
    """
    r = extract_consent(PDF)
    assert r.ok, f"{r.fail_state}: {r.reason}"
    d = r.value

    assert "voice_synth" in d.permitted_uses
    assert "face_replace" not in d.permitted_uses, "granted a right the contract withholds"
    assert "full_replica" not in d.permitted_uses

    assert d.licensee.startswith("Halcyon Pictures")
    assert set(d.territories) == {"US", "CA"}
    assert d.valid_from == "2026-01-01" and d.valid_to == "2028-12-31"
    assert d.citations, "no citations at all"
