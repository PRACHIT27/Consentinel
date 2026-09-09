"""What can reject the model's answer about a clip.

Two kinds of check live here and they are worth telling apart.

**Validators** run inside the harness and reject a *malformed* answer — a
modality outside the vocabulary, a confidence that is not a number. A rejected
answer gets one repair attempt and then resolves to `unverified`.

**`disagrees_with_declaration`** is not a validator. A vendor saying "voice" and
Gemini hearing a face is not a malformed answer; it is two sources disagreeing,
which is exactly the situation a clearance department needs surfaced rather than
resolved. It withholds clearance and says why.
"""

from __future__ import annotations

from typing import Any, Optional

from consentinel.agents.clearance.instructions import MODALITIES


def check_vocabulary(payload: dict[str, Any]) -> Optional[str]:
    """The answer must use our words, not its own."""
    modality = (payload.get("modality") or "").strip().lower()
    if modality not in MODALITIES:
        return (f"modality was {modality!r}; it must be one of "
                f"{', '.join(MODALITIES)}")
    return None


def check_confidence(payload: dict[str, Any]) -> Optional[str]:
    value = payload.get("confidence")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"confidence was {value!r}; it must be a number between 0 and 1"
    if not 0.0 <= number <= 1.0:
        return f"confidence was {number}; it must be between 0 and 1"
    return None


def check_description(payload: dict[str, Any]) -> Optional[str]:
    """One sentence, and a bounded one.

    The length cap is not cosmetic. This text is shown on screen next to a
    verdict, and a model that starts narrating is a model that has started
    reasoning about permission — which is not its job.
    """
    text = (payload.get("description") or "").strip()
    if not text:
        return "description was empty; describe what is in the file in one sentence"
    if len(text) > 240:
        return f"description was {len(text)} characters; keep it under 200"
    return None


VALIDATORS = (check_vocabulary, check_confidence, check_description)


def disagrees_with_declaration(read_modality: str, declared_modality: str) -> Optional[str]:
    """Do the vendor's delivery note and what we perceived agree?

    `none` is handled by the caller as its own case — a file with nobody in it
    is a different problem from a file with the wrong person in it.

    Why this withholds clearance instead of picking a side: the declaration is
    what the production is asserting to a distributor, and if it does not match
    the file, the paperwork is wrong somewhere. Clearing it on either reading
    would be signing off on a document we have reason to doubt.
    """
    read = (read_modality or "").strip().lower()
    declared = (declared_modality or "").strip().lower()
    if not read or not declared or read == declared:
        return None
    if read == "none":
        return None
    if declared == "performance" and read in ("face", "voice"):
        # A whole performance contains both. Perceiving one part of what was
        # declared is agreement, not conflict.
        return None
    return (f"the delivery note says this is a synthetic {declared}, but what we "
            f"perceived in the file is a {read}")
