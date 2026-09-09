"""Two more sample agreements, so the contract reader can be tested on more
than one outcome.

`make_contract_pdf.py` produces the worked example — Mira Vance and Halcyon
Pictures, voice granted in the US and Canada, visual likeness expressly
withheld. One document demonstrates that extraction works. It demonstrates
nothing about the cases that actually catch people out:

    theo_marchand_meridian.pdf   two rights granted, two territories, and a
                                 term that ends in six weeks. Reads as a
                                 perfectly ordinary live grant

    ines_cabral_cascade.pdf      worldwide, and a clause that *looks* like a
                                 second grant but withholds. "Producer shall
                                 not" in the middle of a paragraph of things
                                 Producer may do is the sentence a hurried
                                 reader turns into a permission

The second one is the interesting test. Getting it right means reading a
negative clause as a negative, and the whole registry rests on that: a clause
saying a right is *not* granted is not a grant.

    python -m tools.make_sample_contracts

Fictional throughout. Every page is footered as a synthetic demo document.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer  # noqa: E402

from tools.make_contract_pdf import _footer, _styles  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "docs"


def _doc(path: Path):
    return SimpleDocTemplate(
        str(path), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
        title=path.stem, author="Consentinel demo fixtures",
    )


def _boilerplate(P, artist, producer, agency, picture, effective):
    """The parts every agreement shares, so the differences stand out."""
    return [
        P("PERFORMER ENGAGEMENT AND DIGITAL REPLICATION AGREEMENT", "title"),
        P(f"This Agreement is made as of {effective} between {producer} (the "
          f"&quot;Producer&quot;) and {artist} (the &quot;Artist&quot;), represented by {agency}."),
        P("1. RECITALS", "h"),
        P(f"Producer is engaged in the production of &quot;{picture}&quot; (the "
          f"&quot;Picture&quot;). Artist is a professional performer whose name, voice and likeness "
          f"have independent commercial value. The parties wish to record the limited terms on which "
          f"Producer may create and use a digital replica of Artist."),
        P("2. DEFINITIONS", "h"),
        P("&quot;Digital Replica&quot; means any computer-generated reproduction of the Artist's "
          "voice, visual likeness, or performance, however created, including by machine learning "
          "or other generative means."),
        P("&quot;Term&quot; means the period beginning on the Effective Date and ending on the date "
          "stated in clause 6, after which no right granted here survives."),
    ]


def theo_marchand() -> Path:
    """Two rights, two territories, and a term that is nearly up."""
    s = _styles()
    P = lambda t, k="body": Paragraph(t, s[k])  # noqa: E731
    path = OUT / "theo_marchand_meridian.pdf"

    f = _boilerplate(P, "Théo Marchand", "Meridian Streaming SAS", "Ardent Talent Group",
                     "Lanternes", "1 March 2025")
    f += [
        P("3. SERVICES", "h"),
        P("Artist shall render performance services on such dates as the parties agree, and shall "
          "attend one recording session for the purpose of capturing reference material."),
        PageBreak(),

        P("4. GRANT OF RIGHTS", "h"),
        P("Licensee may generate a synthetic reproduction of the Artist's voice and visual likeness "
          "for the Series, in France and Belgium."),
        P("The grant in this clause is limited to the Picture and to the Term. It does not extend to "
          "advertising, to merchandising, or to any production other than the Picture."),
        P("5. COMPENSATION", "h"),
        P("Producer shall pay Artist a per-episode fee on any synthetic line included in a delivered "
          "episode, payable within forty-five (45) days of delivery."),

        P("6. TERM", "h"),
        P("This Agreement takes effect on 1 March 2025 and expires eighteen (18) months thereafter. "
          "On expiry, Producer shall cease all use of the Digital Replica and shall not renew this "
          "grant by conduct, course of dealing, or continued payment."),
        PageBreak(),

        P("7. RESERVED RIGHTS", "h"),
        P("All rights not expressly granted are reserved to the Artist."),
        P("8. GOVERNING LAW", "h"),
        P("This Agreement is governed by the laws of France."),
        Spacer(1, 24),
        P("SIGNED for and on behalf of Meridian Streaming SAS<br/><br/>"
          "_______________________________<br/>Authorised signatory", "sig"),
        P("SIGNED by Théo Marchand<br/><br/>_______________________________", "sig"),
    ]
    _doc(path).build(f, onFirstPage=_footer, onLaterPages=_footer)
    return path


def ines_cabral() -> Path:
    """Worldwide, and a withholding clause sitting inside a granting paragraph."""
    s = _styles()
    P = lambda t, k="body": Paragraph(t, s[k])  # noqa: E731
    path = OUT / "ines_cabral_cascade.pdf"

    f = _boilerplate(P, "Inês Cabral", "Cascade Films Ltd", "Ardent Talent Group",
                     "A Maré", "12 May 2026")
    f += [
        P("3. SERVICES", "h"),
        P("Artist shall record dialogue for the Picture and shall make herself available for "
          "additional dialogue recording as reasonably required."),
        PageBreak(),

        P("4. GRANT OF RIGHTS", "h"),
        P("Producer may synthesise the Artist's voice for dubbing in any territory in which the "
          "Picture is distributed."),
        # The sentence the reader has to get right. It is deliberately placed
        # among grants rather than in its own "reserved rights" clause, because
        # that is where it hides in a real document.
        P("Producer shall not generate, exploit, or authorise any third party to generate a "
          "synthetic visual likeness of the Artist, and nothing in this clause shall be construed as "
          "permitting the same."),
        P("Producer may retain the recordings made under clause 3 for archival purposes."),
        P("5. COMPENSATION", "h"),
        P("Producer shall pay Artist a per-title fee plus a residual on each territory in which a "
          "synthetic dub is exhibited."),
        PageBreak(),

        P("6. TERM", "h"),
        P("This Agreement takes effect on 12 May 2026 and continues for a period of two (2) years."),
        P("7. RESERVED RIGHTS", "h"),
        P("All rights not expressly granted are reserved to the Artist. For the avoidance of doubt, "
          "the Artist's visual likeness is not licensed under this Agreement."),
        P("8. GOVERNING LAW", "h"),
        P("This Agreement is governed by the laws of Portugal."),
        Spacer(1, 24),
        P("SIGNED for and on behalf of Cascade Films Ltd<br/><br/>"
          "_______________________________<br/>Authorised signatory", "sig"),
        P("SIGNED by Inês Cabral<br/><br/>_______________________________", "sig"),
    ]
    _doc(path).build(f, onFirstPage=_footer, onLaterPages=_footer)
    return path


def main() -> int:
    for build in (theo_marchand, ines_cabral):
        path = build()
        print(f"wrote {path.relative_to(OUT.parent.parent)} "
              f"({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
