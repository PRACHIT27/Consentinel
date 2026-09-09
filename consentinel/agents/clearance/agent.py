"""WU-17 — the clearance check. Our own footage, before it ships.

A production hands us a clip and a delivery note. We answer one question: **is
there a signed permission slip that covers this?** Three answers only — fine to
ship, blocked, or unchecked — and the last one is the default, because absence
of paperwork is not permission.

The shape of the thing is the point of the whole product:

    the same rule engine that judges a stranger's website judges our own clip

Nothing new decides anything here. `agents/reconciler.py` — plain code, no
model — takes the same `Observation` it takes from a web finding, and the only
difference is where the fields came from: a delivery note instead of a page.
Two directions, one set of rules, so a studio cannot get a different answer
about itself than it would get about somebody else.

**What the model does and does not do.** Gemini looks at the file and reports
what is perceptible in it: a voice, a face, a whole performance, or nobody. It
is never asked who the person is, and it is never asked whether the file was
generated. Its answer can only ever *withhold* clearance — by disagreeing with
the delivery note, or by finding no person at all. It cannot grant clearance,
because clearance comes from a contract record the model never sees.

**What we keep.** The file's SHA-256 and the declaration. Not the file. The
uploads bucket exists, but the web service holds no write access to it on
purpose, and a fingerprint is what makes an answer re-checkable later anyway.

The prompt lives in `instructions.py`; what can reject an answer lives in
`guardrail.py`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from consentinel.agents.clearance.guardrail import (
    VALIDATORS,
    disagrees_with_declaration,
)
from consentinel.agents.clearance.instructions import (
    ACCEPTED_MIME,
    INSTRUCTION,
    MAX_INLINE_BYTES,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
)
from consentinel.agents.common.gemini import generate_json_about_media
from consentinel.agents.reconciler import Observation, evaluate
from consentinel.harness.policy import FailState, HarnessPolicy
from consentinel.harness.ports import HarnessDeps
from consentinel.harness.result import HarnessResult
from consentinel.harness.runner import Harness
from consentinel.store.base import Asset, ClearanceState, Consent, Modality, Verdict

POLICY = HarnessPolicy(
    agent_name="clearance",
    fail_state=FailState.UNVERIFIED,   # a check that fails leaves the clip unchecked
    tools=(),                          # looks at a file, calls nothing
    output_schema=dict,
    temperature=0.0,
    cache="content",                   # a file's bytes never change, so its hash is the key
    timeout_s=120.0,                   # video takes longer than text
)

# How a rule-engine verdict lands on a clearance answer. One mapping, in one
# place, so nothing downstream re-decides it. Note what is missing: there is no
# path from a failure to `cleared`.
_STATE_FOR_VERDICT = {
    Verdict.AUTHORIZED: ClearanceState.CLEARED,
    Verdict.UNAUTHORIZED: ClearanceState.BLOCKED,
    Verdict.AMBIGUOUS: ClearanceState.UNVERIFIED,
}


@dataclass(frozen=True)
class Declaration:
    """What the production says about the file it is submitting.

    This is a delivery note, not a finding: the studio knows who is in its own
    shot, who is shipping it, and where it is being released. Those are the
    facts the rule engine needs, and asking a model to guess them when the
    submitter already knows them would be worse in every direction.
    """

    performer_id: str
    licensee: str                       # who is shipping it — must hold the grant
    modality: str                       # voice | face | performance
    territories: tuple[str, ...] = ()   # where it is being released
    vendor: Optional[str] = None
    invoice_ref: Optional[str] = None
    shot_code: Optional[str] = None
    production_id: str = ""
    synthetic: str = "unknown"          # yes | no | unknown, as declared

    def as_sentence(self) -> str:
        """The declaration in one line, for the record and for the screen.

        This is what the rule engine cites. A verdict about our own footage has
        to rest on something a person can read back, and here that something is
        the note the submitter signed rather than a quote from a web page.
        """
        where = ", ".join(self.territories) or "no territory stated"
        made_by = self.vendor or "vendor not recorded"
        return (f"{made_by} delivered a synthetic {self.modality} of this performer "
                f"for {self.licensee}, for release in {where}"
                + (f" (invoice {self.invoice_ref})" if self.invoice_ref else ""))


@dataclass
class ClearanceOutcome:
    """The answer, and everything needed to argue with it."""

    state: ClearanceState
    reasoning: str
    content_hash: str
    matched_consent_id: Optional[str] = None
    declared_modality: str = ""
    perceived_modality: Optional[str] = None
    perceived_description: Optional[str] = None
    perceived_confidence: Optional[float] = None
    check: str = ""
    grants_considered: int = 0
    read_from_cache: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def cleared(self) -> bool:
        return self.state == ClearanceState.CLEARED


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(digest: str) -> str:
    return f"clearance:{digest}:{PROMPT_VERSION}"


def check_asset(
    *,
    data: bytes,
    filename: str,
    mime_type: str,
    declaration: Declaration,
    consents: Sequence[Consent],
    as_of: Optional[datetime] = None,
    deps: Optional[HarnessDeps] = None,
    describe: Optional[Any] = None,
) -> ClearanceOutcome:
    """Check one file against the registry.

    Returns an outcome in every case, including every failure. There is no
    exception path that leaves a caller guessing, because the one thing that
    must never happen here is a crash being read as a pass.

    `describe(instruction, data, mime_type, schema)` does the model call.
    Injected so tests run without the network; defaults to Gemini.
    """
    digest = content_hash(data)
    base = dict(content_hash=digest, declared_modality=declaration.modality)

    def unchecked(reason: str, **extra: Any) -> ClearanceOutcome:
        return ClearanceOutcome(state=ClearanceState.UNVERIFIED, reasoning=reason,
                                check="not_established", **base, **extra)

    # ---- refuse what we cannot read, before spending a model call ----------
    if not data:
        return unchecked("the upload was empty, so there was nothing to check")
    if mime_type not in ACCEPTED_MIME:
        return unchecked(
            f"we cannot read {mime_type or 'that file type'} — audio, video and "
            "images only, and the clip has to be a file rather than a link")
    if len(data) > MAX_INLINE_BYTES:
        return unchecked(
            f"the file is {len(data) // (1024 * 1024)} MB; this build reads up to "
            f"{MAX_INLINE_BYTES // (1024 * 1024)} MB in one piece. Submit a shorter "
            "excerpt of the same shot")

    # ---- ask what is in the file ------------------------------------------
    def call(inst: str, payload: bytes, mime: str, schema: dict[str, Any]) -> dict[str, Any]:
        return generate_json_about_media(inst, payload, mime, schema,
                                         temperature=POLICY.temperature)

    do_describe = describe or call

    def invoke(repair_hint: Optional[str]) -> dict[str, Any]:
        instruction = INSTRUCTION
        if repair_hint:
            instruction += (
                "\n\nA previous answer was rejected. Fix this and answer again:\n"
                f"{repair_hint}"
            )
        return do_describe(instruction, data, mime_type, RESPONSE_SCHEMA)

    harness = Harness(POLICY, deps or HarnessDeps(prompt_version=PROMPT_VERSION))
    read = harness.run(
        invoke,
        subject_id=filename,
        cache_key=cache_key(digest),
        validators=VALIDATORS,
    )

    if not read.ok:
        # Fail toward doubt. We could still run the rules on the declaration
        # alone, and that is exactly the temptation to refuse: clearing a file
        # nobody managed to open would make the check theatre.
        return unchecked(
            "we could not read the file, so nothing here is settled — a "
            f"declaration on its own does not clear a clip ({read.reason})")

    payload = read.value or {}
    perceived = (payload.get("modality") or "").strip().lower()
    seen = dict(
        perceived_modality=perceived,
        perceived_description=(payload.get("description") or "").strip() or None,
        perceived_confidence=float(payload.get("confidence") or 0.0),
        read_from_cache=bool(read.from_cache),
    )

    if not payload.get("human_present"):
        return unchecked(
            "we could not perceive a person in this file, so there is nothing to "
            "match against a permission slip. If this is the right clip, the "
            "reading is wrong and a person should look",
            **seen)

    conflict = disagrees_with_declaration(perceived, declaration.modality)
    if conflict:
        return unchecked(conflict + " — the paperwork and the file disagree, so "
                         "this is not something we will sign off", **seen)

    # ---- the rules decide -------------------------------------------------
    #
    # `actor` is the party shipping it. For our own footage that is the studio
    # named on the delivery note, which is what makes one engine serve both
    # directions: a third party offering the same clip fails the actor check,
    # and our own studio passes it only if it holds the grant.
    observation = Observation(
        performer_id=declaration.performer_id,
        depicts_named_person=True,      # the production declares who is in its own shot
        modality=_as_modality(declaration.modality),
        target_territories=tuple(t.strip().upper() for t in declaration.territories
                                 if (t or "").strip()),
        actor=declaration.licensee,
        confidence=float(seen["perceived_confidence"] or 0.0),
        evidence_quote=declaration.as_sentence(),
    )
    verdict = evaluate(observation, consents, as_of=as_of)

    return ClearanceOutcome(
        state=_STATE_FOR_VERDICT.get(verdict.verdict, ClearanceState.UNVERIFIED),
        reasoning=verdict.reason,
        matched_consent_id=verdict.matched_consent_id,
        check=verdict.check,
        grants_considered=verdict.grants_considered,
        **base,
        **seen,
    )


def _as_modality(value: str) -> Optional[str]:
    """The declared modality, in the vocabulary the rule engine knows.

    Anything else becomes `None`, which the engine reads as "no modality
    established" and answers `ambiguous` — the right answer for a declaration
    we did not understand.
    """
    try:
        return Modality((value or "").strip().lower()).value
    except ValueError:
        return None


def to_asset(
    outcome: ClearanceOutcome,
    declaration: Declaration,
    *,
    asset_id: str,
    filename: str,
) -> Asset:
    """The row we store. Built here so the web layer cannot invent a state that
    the check did not produce."""
    return Asset(
        id=asset_id,
        production_id=declaration.production_id,
        filename=filename,
        shot_code=declaration.shot_code,
        content_hash=outcome.content_hash,
        provenance_metadata={
            "declared_by": declaration.licensee,
            "declared_modality": declaration.modality,
            "declared_territories": list(declaration.territories),
            "declaration": declaration.as_sentence(),
            "perceived_modality": outcome.perceived_modality,
            "perceived_description": outcome.perceived_description,
            "perceived_confidence": outcome.perceived_confidence,
            "check": outcome.check,
            "grants_considered": outcome.grants_considered,
            "read_from_cache": outcome.read_from_cache,
        },
        vendor=declaration.vendor,
        invoice_ref=declaration.invoice_ref,
        performer_id=declaration.performer_id,
        synthetic=declaration.synthetic,
        detected_modality=_detected(outcome.perceived_modality),
        clearance_state=outcome.state,
        matched_consent_id=outcome.matched_consent_id,
        reasoning=outcome.reasoning,
        created_at=datetime.now(timezone.utc),
    )


def _detected(value: Optional[str]) -> Optional[Modality]:
    try:
        return Modality((value or "").strip().lower())
    except ValueError:
        return None


# --------------------------------------------------------------------------
# ADK wiring
# --------------------------------------------------------------------------

def build_agent(model: Optional[str] = None, *, instruction: Optional[str] = None) -> Any:
    """The ADK `LlmAgent` for the media read — WU-24 deploys this.

    This is the only model call in the clearance path, and `output_schema` is
    what keeps it that narrow: with it set, ADK refuses tools and transfer, so
    the step can do nothing but fill in `MediaRead`. It cannot reach the
    registry, which is precisely why its answer can withhold clearance but
    never grant it.
    """
    from google.adk.agents import LlmAgent
    from google.genai import types

    from consentinel.agents.clearance.instructions import MediaRead

    return LlmAgent(
        name="ClearanceInspector",
        model=model or os.environ.get("CONSENTINEL_MODEL", "gemini-2.5-flash"),
        instruction=instruction or INSTRUCTION,
        output_schema=MediaRead,
        output_key="media_read",
        include_contents="none",       # one file, one reading, no history
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=POLICY.temperature),
    )
