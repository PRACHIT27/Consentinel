"""What can reject the model's answer.

Separate file because this is the security surface of the step. A reviewer
asking "what stops it inventing a citation?" should find one short file, not a
function buried three hundred lines into the agent.
"""

from __future__ import annotations

import re
from typing import Any

from consentinel.agents.consent_ingest.instructions import USES
from consentinel.harness.errors import ValidationError


# Punctuation the model re-typesets when it copies a sentence. Straight quotes
# come back curly, hyphens come back as dashes. These are rendering differences,
# not content differences — the same class of thing as a line break — so they are
# folded before comparing. Nothing else is tolerated: a sentence that was never
# in the document still cannot match.
PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    " ": " ", "…": "...", "﻿": "",
}
PUNCT_RE = re.compile("|".join(map(re.escape, PUNCT)))


def norm(text: str) -> str:
    """Fold whitespace and quote/dash styling, and nothing else.

    Two things move between the document and the model's copy of it. PDF
    extraction reflows lines, so a sentence can arrive with a newline in the
    middle. And a model copying text will often re-typeset punctuation — we
    watched Gemini return curly quotes for a sentence the page renders straight.

    Both are how the characters are *drawn*, not what they *say*, so both are
    folded. Anything looser than this and a quote that was never in the document
    could start matching, which is the whole thing this check exists to stop.
    """
    text = PUNCT_RE.sub(lambda m: PUNCT[m.group()], text)
    return re.sub(r"\s+", " ", text).strip()

def check_citations_are_real(pages: list[str]):
    """Every quote must appear verbatim on the page it claims.

    This is the guardrail that costs one string comparison and makes an invented
    citation impossible. A model that cannot find the sentence has to say so.
    """
    normalised = [norm(p) for p in pages]

    def validate(payload: dict[str, Any]) -> None:
        bad: list[str] = []
        for c in payload.get("citations", []):
            page = c.get("page")
            quote = norm(str(c.get("quote", "")))
            if not quote:
                bad.append(f"{c.get('field')}: empty quote")
                continue
            if not isinstance(page, int) or not 1 <= page <= len(normalised):
                bad.append(f"{c.get('field')}: page {page} is not in a {len(normalised)}-page document")
                continue
            if quote not in normalised[page - 1]:
                found = [i + 1 for i, t in enumerate(normalised) if quote in t]
                bad.append(
                    f"{c.get('field')}: quote is not on page {page}"
                    + (f" (it is on page {found[0]})" if found else " (not on any page)")
                )
        if bad:
            raise ValidationError("; ".join(bad))

    return validate


def check_vocabulary(payload: dict[str, Any]) -> None:
    """Uses must be from our list; territories must look like country codes."""
    problems: list[str] = []

    for u in payload.get("permitted_uses", []):
        if u not in USES:
            problems.append(f"permitted_uses: {u!r} is not one of {USES}")

    for t in payload.get("territories", []):
        if t != "WORLDWIDE" and not re.fullmatch(r"[A-Z]{2}", str(t)):
            problems.append(f"territories: {t!r} is not a two-letter country code or WORLDWIDE")

    for key in ("valid_from", "valid_to"):
        v = payload.get(key, "")
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
            problems.append(f"{key}: {v!r} is not YYYY-MM-DD")

    if problems:
        raise ValidationError("; ".join(problems))
