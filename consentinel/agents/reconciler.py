"""WU-12 — the reconciler. Where a verdict is actually decided.

FR-4, `ARCHITECTURE.md` diagram 7. **There is no model call in this module and
there must never be one.** The model observes; code decides. That is what makes
a verdict testable, explainable, and immune to persuasion — a page can argue
with a language model, but it cannot argue with a set comparison.

Two absences are load-bearing, so they are stated rather than implied:

* **No page text is reachable from here** (FR-4.3, hard rule 2). `Observation`
  has no text field and no way to acquire one. The only page-derived string
  that reaches this module is `evidence_quote`, which the WU-10 validators have
  already proved is a verbatim substring of the page — a citation, not content.
* **No cache** (FR-4.5, hard rule 7). Grants expire and get revoked; a stored
  verdict would keep asserting yesterday's answer. Recompute on read — the
  expensive inputs are cached upstream, and this is set arithmetic.

The rule order is FR-4.2's, with three additions that the flow chart does not
cover because it assumes the happy shape of the data. Each one resolves toward
doubt, never toward permission (hard rule 10):

* **Several grants.** A performer usually has more than one. A use is
  authorised if *any* grant covers it; when none does, the explanation names
  the grant that came closest, because "no grant covers this" is useless to a
  human holding four contracts.
* **Unknown target territory.** An empty territory set is vacuously a subset of
  any grant, which would make "we could not tell where this is aimed" pass as
  authorised. It resolves to `ambiguous` instead — unless the grant is
  worldwide, where scope cannot be breached.
* **Unknown actor.** We often cannot tell who is selling. Not knowing is not
  the same as knowing it is a third party, so it is `ambiguous` rather than
  `unauthorized`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from consentinel.agents.validators import REVIEW_THRESHOLD
from consentinel.harness import HarnessDeps
from consentinel.store.base import (
    WORLDWIDE,
    Consent,
    Modality,
    PermittedUse,
    Verdict,
)
from consentinel.tools.contracts import TriageExtraction

AGENT_NAME = "Reconciler"

log = logging.getLogger("consentinel.reconciler")


# --------------------------------------------------------------------------
# Check names. These are shown to humans and asserted in tests, so they are
# constants rather than inline strings.
# --------------------------------------------------------------------------

PERFORMER_NOT_IDENTIFIED = "performer_not_identified"
NO_GRANT = "no_grant"
MODALITY_NOT_PERMITTED = "modality_not_permitted"
TERRITORY_OUTSIDE_GRANT = "territory_outside_grant"
GRANT_EXPIRED = "grant_expired"
GRANT_NOT_YET_EFFECTIVE = "grant_not_yet_effective"
ACTOR_NOT_LICENSEE = "actor_not_licensee"
ACTOR_UNKNOWN = "actor_unknown"
TERRITORIES_UNKNOWN = "territories_unknown"
MODALITY_UNKNOWN = "modality_unknown"
CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
NO_CITATION = "no_citation"
COVERED_BY_GRANT = "covered_by_grant"

CHECK_ORDER = (
    PERFORMER_NOT_IDENTIFIED,
    NO_GRANT, MODALITY_NOT_PERMITTED, TERRITORY_OUTSIDE_GRANT,
    GRANT_EXPIRED, GRANT_NOT_YET_EFFECTIVE, ACTOR_NOT_LICENSEE,
    CONFIDENCE_BELOW_THRESHOLD, COVERED_BY_GRANT,
)
"""FR-4.2's order. `_stage` uses the position to decide which of several
failing grants gets to explain itself."""

_MODALITY_PERMISSIONS: dict[str, frozenset[PermittedUse]] = {
    Modality.VOICE.value: frozenset({PermittedUse.VOICE_SYNTH,
                                     PermittedUse.FULL_REPLICA}),
    Modality.FACE.value: frozenset({PermittedUse.FACE_REPLACE,
                                    PermittedUse.FULL_REPLICA}),
    Modality.PERFORMANCE.value: frozenset({PermittedUse.FULL_REPLICA}),
}
"""Which permissions cover which observed modality.

`ARCHIVAL_REUSE` deliberately covers **nothing** here: permission to reuse
existing footage is not permission to synthesise a new performance, and
conflating the two would authorise the exact thing this product exists to
catch. Flagged in the PR — if the team reads that permission differently, this
table is the one place to change.
"""


class MissingCitation(Exception):
    """A decisive verdict was attempted with nothing to cite.

    FR-4.4: verdicts without a citation are rejected. Raised at the storage
    boundary rather than during evaluation, so one uncitable page cannot take
    down a sweep — see `VerdictResult.assert_citable`.
    """


# --------------------------------------------------------------------------
# Input: structured fields only
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    """What the reconciler is allowed to know.

    Frozen and deliberately narrow. There is no `page_text` field, and adding
    one would break FR-4.3 — if you find yourself wanting it here, the thing
    you actually want is another *validated* field on `TriageExtraction`.
    """

    performer_id: str
    depicts_named_person: bool = False
    modality: Optional[str] = None
    target_territories: tuple[str, ...] = ()
    actor: Optional[str] = None            # who is offering it, if we know
    confidence: float = 0.0
    evidence_quote: Optional[str] = None   # a citation, already proved verbatim

    @classmethod
    def from_extraction(cls, extraction: TriageExtraction, performer_id: str,
                        actor: Optional[str] = None) -> Observation:
        """Build from a **validated** extraction (WU-10 has already run)."""
        return cls(
            performer_id=performer_id,
            depicts_named_person=extraction.depicts_named_person,
            modality=extraction.modality,
            target_territories=tuple(t.strip().upper()
                                     for t in extraction.target_territories
                                     if (t or "").strip()),
            actor=actor,
            confidence=float(extraction.confidence or 0.0),
            evidence_quote=extraction.evidence_quote,
        )


@dataclass(frozen=True)
class VerdictResult:
    """A verdict, the check that produced it, and what it cites.

    Every field here exists so a human can be shown *why*. A verdict a reviewer
    cannot interrogate is a verdict they have to take on faith, which is the
    opposite of the point.
    """

    verdict: Verdict
    check: str
    reason: str
    matched_consent_id: Optional[str] = None
    breached_consent_id: Optional[str] = None
    citation: Optional[str] = None
    territories_outside: tuple[str, ...] = ()
    grants_considered: int = 0
    as_of: Optional[datetime] = None

    @property
    def decisive(self) -> bool:
        """True for verdicts that assert something about the world."""
        return self.verdict in (Verdict.AUTHORIZED, Verdict.UNAUTHORIZED)

    def assert_citable(self) -> VerdictResult:
        """FR-4.4, at the boundary where it bites: raise rather than store.

        `ambiguous` is exempt — it asserts nothing, and a page that supports no
        quote is precisely what it is for.
        """
        if self.decisive and not (self.citation or "").strip():
            raise MissingCitation(
                f"{AGENT_NAME}: refusing to store a {self.verdict.value} "
                f"verdict with no evidence quote (check={self.check})")
        return self

    def to_finding_fields(self) -> dict[str, Any]:
        """The three `Finding` columns a verdict owns. Citation included, so a
        caller cannot persist the verdict and forget the evidence."""
        self.assert_citable()
        return {
            "verdict": self.verdict,
            "matched_consent_id": self.matched_consent_id,
            "reasoning": self.reason,
            "evidence_quote": self.citation,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "check": self.check,
            "reason": self.reason,
            "matched_consent_id": self.matched_consent_id,
            "breached_consent_id": self.breached_consent_id,
            "has_citation": bool(self.citation),
            "territories_outside": list(self.territories_outside),
            "grants_considered": self.grants_considered,
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


# --------------------------------------------------------------------------
# One grant, evaluated
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _GrantEvaluation:
    consent: Consent
    check: str
    reason: str
    territories_outside: tuple[str, ...] = ()
    covered: bool = False
    ambiguous: bool = False

    @property
    def stage(self) -> int:
        """How far down FR-4.2's chain this grant got.

        Used to pick which failing grant explains the verdict: the one that came
        closest is the one a human wants to hear about.
        """
        return CHECK_ORDER.index(self.check) if self.check in CHECK_ORDER else -1


def _evaluate_grant(consent: Consent, observation: Observation,
                    as_of: datetime) -> _GrantEvaluation:
    """FR-4.2 checks (b) to (e) against one grant, in order."""
    # (b) modality
    if observation.modality is None:
        return _GrantEvaluation(
            consent, MODALITY_UNKNOWN,
            "the reading did not establish a modality, so no grant can be "
            "matched to it", ambiguous=True)

    permitted = _MODALITY_PERMISSIONS.get(observation.modality, frozenset())
    granted = {_as_permitted_use(u) for u in consent.permitted_uses}
    if not (permitted & granted):
        return _GrantEvaluation(
            consent, MODALITY_NOT_PERMITTED,
            f"{consent.licensee} holds "
            f"{_join(sorted(u.value for u in granted)) or 'no permitted uses'}, "
            f"which does not cover a synthetic {observation.modality}")

    # (c) territory — sets, not a single value (FR-4.6)
    grant_territories = {t.strip().upper() for t in consent.territories
                         if (t or "").strip()}
    worldwide = WORLDWIDE in grant_territories
    if not observation.target_territories and not worldwide:
        return _GrantEvaluation(
            consent, TERRITORIES_UNKNOWN,
            "the page did not reveal which territories it targets, and "
            f"{consent.licensee}'s grant is limited to "
            f"{_join(sorted(grant_territories))}", ambiguous=True)

    outside = tuple(sorted(set(observation.target_territories) - grant_territories)) \
        if not worldwide else ()
    if outside:
        return _GrantEvaluation(
            consent, TERRITORY_OUTSIDE_GRANT,
            f"the offering targets {_join(outside)} while "
            f"{consent.licensee}'s grant covers only "
            f"{_join(sorted(grant_territories))}",
            territories_outside=outside)

    # (d) validity window
    valid_from = _aware(consent.valid_from)
    valid_to = _aware(consent.valid_to)
    if valid_from and as_of < valid_from:
        return _GrantEvaluation(
            consent, GRANT_NOT_YET_EFFECTIVE,
            f"{consent.licensee}'s grant does not take effect until "
            f"{valid_from.date().isoformat()}")
    if valid_to and as_of > valid_to:
        return _GrantEvaluation(
            consent, GRANT_EXPIRED,
            f"{consent.licensee}'s grant expired on "
            f"{valid_to.date().isoformat()}")

    # (e) actor
    if observation.actor is None:
        return _GrantEvaluation(
            consent, ACTOR_UNKNOWN,
            "the use falls inside "
            f"{consent.licensee}'s grant, but the page did not reveal who is "
            "offering it — not knowing is not the same as knowing it is a "
            "third party", ambiguous=True)
    if not _same_party(observation.actor, consent.licensee):
        return _GrantEvaluation(
            consent, ACTOR_NOT_LICENSEE,
            f"{observation.actor} is offering this; the grant is held by "
            f"{consent.licensee}")

    return _GrantEvaluation(
        consent, COVERED_BY_GRANT,
        f"{consent.licensee} holds "
        f"{_join(sorted(u.value for u in granted))} for "
        f"{_join(sorted(grant_territories))}"
        + (f", valid to {valid_to.date().isoformat()}" if valid_to else ""),
        covered=True)


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------

def evaluate(observation: Observation, consents: Sequence[Consent],
             as_of: Optional[datetime] = None, *,
             strict_citation: bool = False) -> VerdictResult:
    """The rule engine. A pure function: same inputs, same verdict, forever.

    `strict_citation=True` raises `MissingCitation` instead of resolving an
    uncitable decisive verdict to `ambiguous`. The default is off so one
    uncitable page degrades itself rather than the sweep.
    """
    now = _aware(as_of) or datetime.now(timezone.utc)
    mine = [c for c in consents if c.performer_id == observation.performer_id]

    # (0) is this page even about the performer?
    #
    # Not in FR-4.2, because the flow chart starts from "an observation about a
    # performer" and assumes that part. The first live sweep showed why it needs
    # saying: a generic voice-cloning product page mentions nobody, so the name
    # check passes vacuously, and the engine then answers "is this covered by
    # her grants?" about a page that has nothing to do with her. Every answer to
    # that question is wrong, and `unauthorized` is wrong in the direction that
    # accuses a stranger.
    if not observation.depicts_named_person:
        return _finish(VerdictResult(
            verdict=Verdict.AMBIGUOUS, check=PERFORMER_NOT_IDENTIFIED,
            reason=("the page does not name or depict this performer, so no "
                    "verdict about their consent can be reached from it"),
            citation=observation.evidence_quote,
            grants_considered=len(mine), as_of=now), strict_citation)

    # (a) no grant at all
    if not mine:
        return _finish(VerdictResult(
            verdict=Verdict.UNAUTHORIZED, check=NO_GRANT,
            reason="no consent on file for this performer covers any use",
            citation=observation.evidence_quote, grants_considered=0,
            as_of=now), strict_citation)

    evaluations = [_evaluate_grant(c, observation, now) for c in mine]
    covering = next((e for e in evaluations if e.covered), None)

    if covering is not None:
        # (f) confidence — the last gate before asserting permission
        if observation.confidence < REVIEW_THRESHOLD:
            return _finish(VerdictResult(
                verdict=Verdict.AMBIGUOUS, check=CONFIDENCE_BELOW_THRESHOLD,
                reason=(f"the reading scored {observation.confidence:.2f}, "
                        f"below the {REVIEW_THRESHOLD} threshold, so a human "
                        f"decides; {covering.reason}"),
                matched_consent_id=covering.consent.id,
                citation=observation.evidence_quote,
                grants_considered=len(mine), as_of=now), strict_citation)

        return _finish(VerdictResult(
            verdict=Verdict.AUTHORIZED, check=COVERED_BY_GRANT,
            reason=f"authorised: {covering.reason}",
            matched_consent_id=covering.consent.id,
            citation=observation.evidence_quote,
            grants_considered=len(mine), as_of=now), strict_citation)

    # Nothing covers it. Confidence still gates an assertion either way — a
    # low-confidence reading cannot support "unauthorized" any more than it can
    # support "authorized" (DESIGN §3 L5).
    closest = max(evaluations, key=lambda e: (e.stage, not e.ambiguous))
    if observation.confidence < REVIEW_THRESHOLD:
        return _finish(VerdictResult(
            verdict=Verdict.AMBIGUOUS, check=CONFIDENCE_BELOW_THRESHOLD,
            reason=(f"the reading scored {observation.confidence:.2f}, below "
                    f"the {REVIEW_THRESHOLD} threshold, so a human decides; "
                    f"closest grant: {closest.reason}"),
            breached_consent_id=closest.consent.id,
            territories_outside=closest.territories_outside,
            citation=observation.evidence_quote,
            grants_considered=len(mine), as_of=now), strict_citation)

    if closest.ambiguous:
        return _finish(VerdictResult(
            verdict=Verdict.AMBIGUOUS, check=closest.check,
            reason=closest.reason,
            breached_consent_id=closest.consent.id,
            citation=observation.evidence_quote,
            grants_considered=len(mine), as_of=now), strict_citation)

    return _finish(VerdictResult(
        verdict=Verdict.UNAUTHORIZED, check=closest.check,
        reason=f"unauthorised: {closest.reason}",
        breached_consent_id=closest.consent.id,
        territories_outside=closest.territories_outside,
        citation=observation.evidence_quote,
        grants_considered=len(mine), as_of=now), strict_citation)


def _finish(result: VerdictResult, strict_citation: bool) -> VerdictResult:
    """FR-4.4. Without a quote, a decisive verdict is not storable.

    Strict callers get the exception. Everyone else gets `ambiguous` with
    `no_citation` — doubt, which is the direction every gap in this system
    points (hard rule 10).
    """
    if not result.decisive or (result.citation or "").strip():
        return result
    if strict_citation:
        return result.assert_citable()
    return VerdictResult(
        verdict=Verdict.AMBIGUOUS, check=NO_CITATION,
        reason=(f"no quotable evidence for a {result.verdict.value} verdict, "
                f"so a human decides; would have been: {result.reason}"),
        matched_consent_id=result.matched_consent_id,
        breached_consent_id=result.breached_consent_id,
        territories_outside=result.territories_outside,
        grants_considered=result.grants_considered, as_of=result.as_of)


# --------------------------------------------------------------------------
# The agent wrapper — audit only. No cache, no model, no retries.
# --------------------------------------------------------------------------

@dataclass
class Reconciler:
    """`evaluate` plus an audit row. That is the whole component.

    A custom agent rather than an `LlmAgent` (DESIGN Part II §2.1 lists it as
    one of the three with no model at all), and deliberately not run through the
    harness: the harness exists for retries and caching, and a pure function
    needs neither — while caching a verdict is forbidden outright (hard rule 7).
    """

    deps: HarnessDeps = field(default_factory=HarnessDeps)

    def reconcile(self, observation: Observation, consents: Sequence[Consent],
                  as_of: Optional[datetime] = None, *,
                  strict_citation: bool = False) -> VerdictResult:
        result = evaluate(observation, consents, as_of,
                          strict_citation=strict_citation)
        self._record(observation, result)
        return result

    def for_extraction(self, extraction: TriageExtraction, performer_id: str,
                       consents: Sequence[Consent], *,
                       actor: Optional[str] = None,
                       as_of: Optional[datetime] = None) -> VerdictResult:
        """Convenience for the enforcement pipeline."""
        return self.reconcile(
            Observation.from_extraction(extraction, performer_id, actor),
            consents, as_of)

    def _record(self, observation: Observation, result: VerdictResult) -> None:
        """The decision trail. Structured fields only — no page text passes
        through this module, so none can reach the log either."""
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": AGENT_NAME,
            "event": "verdict",
            "subject_id": observation.performer_id,
            "model": None,                 # there is no model in this module
            "depicts_named_person": observation.depicts_named_person,
            "modality": observation.modality,
            "target_territories": list(observation.target_territories),
            "observed_actor": observation.actor,
            "confidence": observation.confidence,
        }
        event.update(result.as_dict())
        self.deps.audit.append(event)

        line = (f"{AGENT_NAME} performer={observation.performer_id} "
                f"verdict={result.verdict.value} check={result.check} "
                f"grants={result.grants_considered} "
                f"citation={bool(result.citation)}")
        if result.territories_outside:
            line += f" outside={','.join(result.territories_outside)}"
        log.info(line)


# --------------------------------------------------------------------------

def _as_permitted_use(value: Any) -> PermittedUse:
    if isinstance(value, PermittedUse):
        return value
    return PermittedUse(str(value))


def _same_party(a: str, b: str) -> bool:
    """Party names compared loosely on punctuation and company suffixes.

    "Aurora Studios" and "Aurora Studios LLC" are the same licensee; anything
    less forgiving would report the licensee as a third party over a comma.
    """
    return _party_key(a) == _party_key(b)


_SUFFIXES = ("llc", "inc", "ltd", "limited", "gmbh", "sa", "srl", "bv", "plc",
             "co", "company", "corp", "corporation", "pictures", "studios")


def _party_key(name: str) -> str:
    words = [w for w in "".join(
        c.lower() if c.isalnum() or c.isspace() else " " for c in (name or "")
    ).split() if w]
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Naive datetimes are read as UTC rather than crashing a comparison."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _join(values: Sequence[str]) -> str:
    return ", ".join(values)
