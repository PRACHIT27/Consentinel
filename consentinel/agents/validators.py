"""WU-10 — the guardrails every extraction passes before it goes anywhere.

`DESIGN.md` §3 L3. Built before WU-09 rather than after, because WU-09's
acceptance is "a valid `TriageExtraction` or an explicit failure", and these
functions are what makes "valid" mean something.

The one that earns its keep:

    **`evidence_quote` must be a verbatim substring of the page text.**

One string comparison, and quote hallucination stops being possible rather than
becoming unlikely. Everything else here is ordinary field checking; that line is
the reason a judge can trust a quote on screen.

Each validator raises `ValidationError` — which the harness classifies as
semantic, so the model gets exactly one repair attempt carrying the message
back, and then the finding resolves to `ambiguous`. A parse failure must never
be retried into a verdict.

Whitespace is normalised before comparing quotes and nothing else is. Every
further liberty — lowercasing, stripping punctuation, fuzzy matching the quote —
widens the gap a fabricated quote can hide in.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, Optional, Sequence

from consentinel.harness import ValidationError
from consentinel.store.base import WORLDWIDE, Modality, Performer
from consentinel.tools.contracts import TriageExtraction

MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

MIN_QUOTE_CHARS = 8
"""A three-character "quote" is a substring of almost any page. Below this the
quote proves nothing, which is worse than no quote at all because it looks like
evidence."""

REVIEW_THRESHOLD = 0.6
"""DESIGN §3 L5: below this the finding goes to a human rather than to a
verdict. Not enforced here — this module rejects invalid extractions, it does
not decide. Exported so the reconciler and the UI use one number."""

_WS = re.compile(r"\s+")


def normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace and normalise unicode form.

    NFKC first, because a page can contain a non-breaking space or a full-width
    character where the model reproduced the ordinary one. That is a rendering
    difference, not a fabrication.
    """
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


# --------------------------------------------------------------------------
# The individual checks
# --------------------------------------------------------------------------

def check_quote_is_verbatim(extraction: TriageExtraction, page_text: str) -> None:
    """Hard rule 5. The highest-value guardrail in the system.

    A null quote is allowed — plenty of pages support no claim, and FR-3's
    acceptance is "a quote or an explicit null". A *fabricated* quote is not.
    """
    quote = extraction.evidence_quote
    if quote is None or not quote.strip():
        return

    normalised_quote = normalise_whitespace(quote)
    if len(normalised_quote) < MIN_QUOTE_CHARS:
        raise ValidationError(
            f"evidence_quote {quote!r} is too short to prove anything "
            f"(minimum {MIN_QUOTE_CHARS} characters)")

    if normalised_quote not in normalise_whitespace(page_text):
        raise ValidationError(
            f"evidence_quote is not a verbatim substring of the page: "
            f"{quote!r}. Quote the page exactly or return null.")


def check_person_name_matches(extraction: TriageExtraction,
                              performer: Optional[Performer]) -> None:
    """The name must be the performer we swept for, or an alias.

    A mismatch means the model drifted onto a different person, and everything
    downstream — the verdict, the notice — would be about the wrong human.
    Void, not repairable by guessing.
    """
    if performer is None or not extraction.depicts_named_person:
        return
    name = (extraction.person_name or "").strip()
    if not name:
        raise ValidationError(
            "depicts_named_person is true but person_name is empty")

    candidates = [performer.name, *performer.aliases]
    if not any(_names_match(name, candidate) for candidate in candidates):
        raise ValidationError(
            f"person_name {name!r} does not match the swept performer "
            f"({', '.join(candidates)}). The extraction is about a different "
            "person and is void.")


def _names_match(found: str, candidate: str) -> bool:
    """Deliberately loose, and only loose in safe directions.

    Case, accents, punctuation and initials vary between a legal name and how a
    marketplace listing writes it: "M. Vance", "mira vance", "MIRA VANCE".
    Matching on the surname plus a first initial catches those. What it will not
    do is match a different surname, which is the failure that matters.
    """
    a, b = _name_key(found), _name_key(candidate)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True

    a_parts, b_parts = a.split(), b.split()
    if not a_parts or not b_parts:
        return False
    if a_parts[-1] != b_parts[-1]:
        return False                      # different surname: not the same person
    return a_parts[0][:1] == b_parts[0][:1]


def _name_key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", stripped.lower())
    return normalise_whitespace(cleaned)


def check_territories(extraction: TriageExtraction) -> None:
    """ISO 3166-1 alpha-2, or `WORLDWIDE`.

    Territory drives the verdict — consent is territory-scoped — so a made-up
    code would silently widen or narrow a permission grant.
    """
    for territory in extraction.target_territories:
        code = (territory or "").strip()
        if code == WORLDWIDE:
            continue
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValidationError(
                f"target_territories contains {territory!r}; expected ISO "
                f"3166-1 alpha-2 codes (e.g. BR, US) or {WORLDWIDE}")


def check_confidence(extraction: TriageExtraction) -> None:
    value = extraction.confidence
    if value is None or not isinstance(value, (int, float)):
        raise ValidationError(f"confidence must be a number, got {value!r}")
    if not MIN_CONFIDENCE <= float(value) <= MAX_CONFIDENCE:
        raise ValidationError(
            f"confidence {value!r} is outside [{MIN_CONFIDENCE}, {MAX_CONFIDENCE}]")


def check_modality(extraction: TriageExtraction) -> None:
    modality = extraction.modality
    if modality is None:
        return
    allowed = {m.value for m in Modality}
    if str(modality).strip().lower() not in allowed:
        raise ValidationError(
            f"modality {modality!r} is not one of {sorted(allowed)}")


def check_internal_consistency(extraction: TriageExtraction) -> None:
    """Claims that contradict each other, which is how an injected instruction
    tends to surface once the schema has removed every other channel.

    A page asserting a synthetic copy with no quote to show for it is the
    specific shape of "the page told the model what to answer".
    """
    if extraction.is_synthetic_claim and not (extraction.evidence_quote or "").strip():
        raise ValidationError(
            "is_synthetic_claim is true but no evidence_quote was given; the "
            "claim must be quotable from the page")
    if not extraction.depicts_named_person and (extraction.person_name or "").strip():
        raise ValidationError(
            f"person_name {extraction.person_name!r} was given while "
            "depicts_named_person is false")


# --------------------------------------------------------------------------
# The whole set, as the harness wants it
# --------------------------------------------------------------------------

def validate_extraction(extraction: TriageExtraction, page_text: str,
                        performer: Optional[Performer] = None) -> None:
    """Every check, cheapest first. Raises on the first failure.

    Cheapest first so the repair hint names a field the model can fix in one
    edit, rather than the last thing that happened to be checked.
    """
    check_confidence(extraction)
    check_modality(extraction)
    check_territories(extraction)
    check_internal_consistency(extraction)
    check_person_name_matches(extraction, performer)
    check_quote_is_verbatim(extraction, page_text)


def extraction_validators(page_text: str, performer: Optional[Performer] = None
                          ) -> tuple[Callable[[TriageExtraction], None], ...]:
    """The validator tuple `Harness.run(validators=...)` takes."""
    def validate(extraction: TriageExtraction) -> None:
        validate_extraction(extraction, page_text, performer)

    return (validate,)


def failures(extraction: TriageExtraction, page_text: str,
             performer: Optional[Performer] = None) -> list[str]:
    """Every failure rather than the first, for tests and the review screen."""
    found: list[str] = []
    checks: Sequence[Callable[[], None]] = (
        lambda: check_confidence(extraction),
        lambda: check_modality(extraction),
        lambda: check_territories(extraction),
        lambda: check_internal_consistency(extraction),
        lambda: check_person_name_matches(extraction, performer),
        lambda: check_quote_is_verbatim(extraction, page_text),
    )
    for check in checks:
        try:
            check()
        except ValidationError as exc:
            found.append(str(exc))
    return found


def is_reviewable(extraction: TriageExtraction) -> bool:
    """True when confidence is high enough to reason about without a human.

    DESIGN §3 L5. Below the threshold the finding is `ambiguous` — doubt is
    never resolved by guessing.
    """
    return float(extraction.confidence or 0.0) >= REVIEW_THRESHOLD


def quoted_span(extraction: TriageExtraction, page_text: str
                ) -> Optional[tuple[int, int]]:
    """Where the quote sits in the page, for the decision trail.

    Returns character offsets into the *normalised* text, or None. Prachit's
    WU-23 can highlight the sentence instead of asking the reader to trust that
    it was there.
    """
    quote = normalise_whitespace(extraction.evidence_quote or "")
    if not quote:
        return None
    haystack = normalise_whitespace(page_text)
    start = haystack.find(quote)
    return None if start < 0 else (start, start + len(quote))


def iter_checks() -> Iterable[str]:
    """Names of the checks, for the audit trail and the docs."""
    return ("confidence", "modality", "territories", "consistency",
            "person_name", "evidence_quote")
