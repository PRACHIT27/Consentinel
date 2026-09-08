"""Generate the demo contract PDF for the fictional performer.

    python tools/make_contract_pdf.py

Writes `fixtures/docs/mira_vance_halcyon_agreement.pdf`.

Why this is generated rather than hand-made: the PDF has to agree exactly with
the consent record in `fixtures/seed.json`, down to the page number each clause
sits on. WU-03's validator requires an extracted citation to be a verbatim
substring of the page it claims to come from, so if a clause drifts onto another
page the extraction silently loses that field. Generating it from the same
constants the fixture uses keeps the two in step, and
`tests/test_fixture_contract.py` fails if they ever part company.

Everything here is fictional. Mira Vance, Halcyon Pictures, Ardent Talent Group
and Northlight Post do not exist. The repository and the demo video are public,
so no real person's agreement or likeness appears anywhere.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "docs" / "mira_vance_halcyon_agreement.pdf"

ARTIST = "Mira Vance"
PRODUCER = "Halcyon Pictures LLC"
PICTURE = "Nightfall Over Carson"
AGENCY = "Ardent Talent Group"

# The two sentences the fixture cites, and the page each must land on.
# Keep these byte-identical to `clause_citations` in fixtures/seed.json.
CLAUSE_VOICE = (
    "Producer may generate synthetic voice performances of the Artist solely "
    "for the Picture, in the United States and Canada."
)
CLAUSE_NO_LIKENESS = (
    "No right is granted to generate or exploit a synthetic visual likeness "
    "of the Artist."
)
CLAUSE_PAGES = {CLAUSE_VOICE: 4, CLAUSE_NO_LIKENESS: 5}


def _styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle(
        "body", parent=ss["BodyText"], fontName="Times-Roman", fontSize=10.5,
        leading=15, alignment=TA_JUSTIFY, spaceAfter=8,
    )
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontName="Times-Bold",
                                fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=18),
        "h": ParagraphStyle("h", parent=ss["Heading2"], fontName="Times-Bold",
                            fontSize=11, leading=14, spaceBefore=10, spaceAfter=6),
        "body": body,
        "sig": ParagraphStyle("sig", parent=body, spaceBefore=28, alignment=0),
    }


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillGray(0.45)
    canvas.drawString(
        0.9 * inch, 0.6 * inch,
        "SYNTHETIC DEMO DOCUMENT - fictional parties, generated for Consentinel testing. Not a real agreement.",
    )
    canvas.drawRightString(7.6 * inch, 0.6 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build() -> Path:
    s = _styles()
    P = lambda t, k="body": Paragraph(t, s[k])  # noqa: E731
    f: list = []

    # ---------------------------------------------------------------- page 1
    f += [
        P("PERFORMER ENGAGEMENT AND DIGITAL REPLICATION AGREEMENT", "title"),
        P(f"This Agreement is made as of 1 January 2026 between {PRODUCER} (the "
          f"&ldquo;Producer&rdquo;) and {ARTIST} (the &ldquo;Artist&rdquo;), represented by {AGENCY}."),
        P("1. RECITALS", "h"),
        P(f"Producer is engaged in the production of the theatrical motion picture presently "
          f"entitled &ldquo;{PICTURE}&rdquo; (the &ldquo;Picture&rdquo;). Artist is a professional "
          f"performer whose name, voice and likeness have independent commercial value. The parties "
          f"wish to record the terms on which Artist renders services and the limited terms on which "
          f"Producer may create and use a digital replica of Artist."),
        P("2. DEFINITIONS", "h"),
        P("&ldquo;Digital Replica&rdquo; means any computer-generated reproduction of the Artist's "
          "voice, visual likeness, or performance, however created, including by machine learning or "
          "other generative means."),
        P("&ldquo;Synthetic Voice Performance&rdquo; means audio in which the Artist's voice is "
          "reproduced or generated other than by the Artist's contemporaneous performance."),
        P("&ldquo;Territory&rdquo; means, except where this Agreement provides otherwise, the "
          "United States and Canada."),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- page 2
    f += [
        P("3. ENGAGEMENT", "h"),
        P("Producer engages Artist to render principal photography services in the role of "
          "&ldquo;Della Reyes&rdquo; in the Picture, on the dates set out in Schedule A, subject to "
          "the standard terms of the applicable collective bargaining agreement."),
        P("4. TERM", "h"),
        P("The rights granted under this Agreement commence on 1 January 2026 and expire on "
          "31 December 2028, unless extended by a separate written instrument signed by Artist. "
          "Upon expiry, all rights to create or exploit a Digital Replica revert to Artist "
          "automatically and without further act of either party."),
        P("5. COMPENSATION", "h"),
        P("Producer shall pay Artist the fixed compensation set out in Schedule B for services "
          "rendered. In addition, a per-title fee is payable on any synthetic voice line retained "
          "in final delivery, calculated as set out in Schedule B and payable within forty-five "
          "days of first commercial release."),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- page 3
    f += [
        P("6. CREDIT", "h"),
        P("Artist shall receive credit in the main titles of the Picture in a size and position no "
          "less favourable than that accorded to any other performer in a supporting role."),
        P("7. APPROVALS AND CONSULTATION", "h"),
        P("Producer shall consult with Artist in good faith before the first use of any Digital "
          "Replica in a manner not expressly contemplated by this Agreement. Consultation does not "
          "constitute consent, and no course of dealing shall be construed to enlarge the rights "
          "granted in Clause 8."),
        P("8. CONSENT TO DIGITAL REPLICATION", "h"),
        P("Artist consents to the creation of a Digital Replica of the Artist's voice for the "
          "limited purposes described in Clause 9. Such consent is specific, is limited to the "
          "Picture, and is not transferable to any affiliate, successor, licensee or vendor of "
          "Producer without a separate written instrument signed by Artist."),
        PageBreak(),
    ]

    # ------------------------------------------------- page 4: the voice grant
    f += [
        P("9. PERMITTED SYNTHETIC VOICE USE", "h"),
        P("Subject to Clause 4 and Clause 10, and to payment of the amounts described in "
          "Clause 5, the following rights are granted:"),
        P(f"(a) {CLAUSE_VOICE}"),
        P("(b) Producer may reuse existing archival recordings of the Artist's performance in the "
          "Picture, and in trailers, featurettes and other secondary materials promoting the "
          "Picture, within the Territory."),
        P("(c) The rights in paragraphs (a) and (b) are exercisable only during the term stated in "
          "Clause 4 and only within the Territory. Any exploitation outside the Territory requires "
          "a separate written instrument signed by Artist."),
        P("(d) Producer shall maintain a record of each synthetic voice line generated under "
          "paragraph (a) and retained in final delivery, and shall make that record available to "
          "Artist or Artist's representative on request."),
        PageBreak(),
    ]

    # -------------------------------------------- page 5: rights NOT granted
    f += [
        P("10. RESERVATION OF RIGHTS", "h"),
        P("All rights not expressly granted in Clause 9 are reserved to Artist. In particular:"),
        P(f"(a) {CLAUSE_NO_LIKENESS}"),
        P("(b) No right is granted to use any Digital Replica of the Artist in advertising for any "
          "product or service other than the Picture."),
        P("(c) No right is granted to use any Digital Replica of the Artist to generate dialogue, "
          "performance or endorsement in any production other than the Picture."),
        P("(d) No right is granted to sublicense, sell, publish or otherwise make available any "
          "model, embedding or other artefact derived from the Artist's voice or likeness."),
        P("(e) Any use falling outside Clause 9 is a material breach, and Artist may seek "
          "injunctive relief in addition to any other remedy."),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- page 6
    f += [
        P("11. VENDOR OBLIGATIONS", "h"),
        P("Producer shall procure that each vendor engaged on the Picture is bound by terms no less "
          "protective of Artist than this Agreement, and shall disclose to Artist on request the "
          "identity of any vendor that has generated a Digital Replica under Clause 9."),
        P("12. GENERAL", "h"),
        P("This Agreement is governed by the laws of the State of California. It may be amended only "
          "by a written instrument signed by both parties. If any provision is held unenforceable, "
          "the remainder continues in effect."),
        P("SIGNED for and on behalf of the Producer:", "sig"),
        P("____________________________<br/>Halcyon Pictures LLC"),
        P("SIGNED by the Artist:", "sig"),
        P(f"____________________________<br/>{ARTIST}"),
        Spacer(1, 10),
        P("Schedules A and B omitted from this demonstration copy."),
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(OUT), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
        title=f"{ARTIST} / {PRODUCER} - Performer Engagement and Digital Replication Agreement",
        author="Consentinel demo fixture (synthetic)",
        subject="Fictional agreement generated for testing. Not a real contract.",
    ).build(f, onFirstPage=_footer, onLaterPages=_footer)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
