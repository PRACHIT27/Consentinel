"""The demo contract PDF must agree with the consent record in the fixture.

This guards WU-03 rather than the PDF. Consent ingestion requires an extracted
citation to be a verbatim substring of the page it claims to come from, so if a
clause drifts onto a different page during a regeneration the extraction
silently drops that field and the demo shows an incomplete grant. Cheaper to
fail here.

Whitespace is normalised before comparing, and nothing else — the same rule the
extraction validator uses, because PDF text extraction reflows lines.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "fixtures" / "docs" / "mira_vance_halcyon_agreement.pdf"
SEED = ROOT / "fixtures" / "seed.json"

pytest.importorskip("pypdf")
from pypdf import PdfReader  # noqa: E402

needs_pdf = pytest.mark.skipif(
    not PDF.exists(),
    reason="run tools/make_contract_pdf.py to generate the fixture",
)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def pages() -> list[str]:
    return [norm(p.extract_text() or "") for p in PdfReader(str(PDF)).pages]


@pytest.fixture(scope="module")
def consent() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))["consents"][0]


@needs_pdf
def test_every_citation_is_verbatim_on_its_stated_page(pages, consent):
    for cit in consent["clause_citations"]:
        quote, page = norm(cit["quote"]), cit["page"]
        assert 1 <= page <= len(pages), f"cited page {page} does not exist"
        assert quote in pages[page - 1], (
            f"citation not found verbatim on page {page}. "
            f"Found on {[i + 1 for i, t in enumerate(pages) if quote in t] or 'no page'}. "
            f"Regenerate with tools/make_contract_pdf.py, or fix the page number in seed.json."
        )


@needs_pdf
@pytest.mark.parametrize(
    "label,needle",
    [
        ("licensee", "Halcyon Pictures LLC"),
        ("performer", "Mira Vance"),
        ("valid_from", "commence on 1 January 2026"),
        ("valid_to", "expire on 31 December 2028"),
        ("territories", "United States and Canada"),
        ("archival_reuse grant", "reuse existing archival recordings"),
        ("compensation trigger", "per-title fee is payable on any synthetic voice line retained"),
    ],
)
def test_extractable_facts_are_present(pages, label, needle):
    """Each of the six fields WU-03 populates must be findable in the text.
    If one of these disappears, extraction will return null for that field and
    the failure will look like a model problem rather than a fixture problem."""
    assert needle in " ".join(pages), f"{label} is not stated in the contract text"


@needs_pdf
def test_contract_withholds_visual_likeness(pages, consent):
    """The blocked asset in the demo (asset_0533, a synthetic face) depends on
    this grant NOT covering visual likeness. If the contract ever grants it, the
    clearance demo loses its red row."""
    assert "face_replace" not in consent["permitted_uses"]
    assert "full_replica" not in consent["permitted_uses"]
    assert "No right is granted to generate or exploit a synthetic visual likeness" in " ".join(pages)


@needs_pdf
def test_marked_as_synthetic(pages):
    """Public repo, public video. The document must say what it is on every page."""
    for i, text in enumerate(pages, 1):
        assert "SYNTHETIC DEMO DOCUMENT" in text, f"page {i} lacks the synthetic marker"
