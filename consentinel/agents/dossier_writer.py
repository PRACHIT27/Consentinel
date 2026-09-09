"""WU-16 — the case file, and the draft notice that comes with it.

FR-5.2 to FR-5.5. Two halves:

1. **`build_bundle`** — assemble the evidence: the URL, the snapshot URIs and
   their hashes, the quote from the page, the breached clause *with its own
   citation*, and the verdict reasoning. Deterministic, no model.
2. **`draft`** — generate a notice with Gemini, grounded strictly in that
   bundle, and then *verify* the grounding rather than trusting it.

**Nothing here can send anything.** Hard rule 6 and FR-5.5: notices are
drafted, humans send them. There is no email client, no webhook, no outbound
anything in this module, and `tests/test_dossier_writer.py` greps the whole
codebase to keep it that way. An agent that can act on a mistaken verdict
against a third party is a liability, and the absence has to be structural
rather than a policy somebody remembers.

**The grounding check is the interesting part.** A generated notice is exactly
the place a language model will helpfully invent a statute, a deadline or a URL.
So every URL and every quoted span in the draft must appear in the bundle;
anything else is rejected and regenerated once, then the draft is refused. The
model gets to phrase the letter. It does not get to add facts.

**It states evidence and a rule mismatch, and stops there** (NG-4). "This
listing offers a synthetic voice of X; the grant on file covers US and CA and
this offering targets BR" is a fact. "This constitutes infringement under
17 U.S.C. §106" is a legal conclusion we are not qualified to assert, and the
instruction says so.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from consentinel.agents.reconciler import VerdictResult
from consentinel.agents.snapshot import SnapshotRef
from consentinel.harness import (
    FailState,
    Harness,
    HarnessDeps,
    HarnessPolicy,
    ValidationError,
)
from consentinel.store.base import Consent, Finding, Performer, Verdict

AGENT_NAME = "DossierWriter"

DEFAULT_MODEL = os.environ.get("CONSENTINEL_DOSSIER_MODEL", "gemini-2.5-flash")

TEMPERATURE = 0.3
"""The one place sampling is acceptable (CLAUDE.md conventions): a notice that
reads like a form letter gets ignored. Still low — the facts come from the
bundle and only the phrasing is free."""

ARMOR_TEMPLATE_OUT = "consentinel-notice-out"
"""Model Armor on the way **out**, inspect-and-block (DESIGN Part III §3). A
notice must never carry someone's personal data or a link to a malware site.
This is the asymmetric twin of triage's inspect-only template."""

MIN_QUOTED_CHARS = 12
"""Shorter quoted spans in a draft are ordinary phrasing ("no consent"), not
claimed quotations, and treating them as citations would reject every honest
letter."""

_URL_RE = re.compile(r"https?://[^\s<>\"'\])}]+", re.IGNORECASE)
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{4,})[\"”]")

log = logging.getLogger("consentinel.dossier_writer")


class UngroundedDraft(ValidationError):
    """The draft asserted something the bundle does not contain.

    A `ValidationError`, so the harness treats it as semantic: one repair
    carrying the specific offending fact back to the model, then the draft is
    refused. A notice is an outward-facing document; "nearly grounded" is not a
    standard it gets to meet.
    """


# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ClauseCitation:
    """The specific clause the case rests on, and where it is written down.

    FR-5's acceptance is that the case file *names the clause*. A notice that
    says "you are outside the terms" without pointing at the sentence is a
    notice the recipient can dismiss.
    """

    consent_id: str
    licensee: str
    text: Optional[str] = None          # the clause, verbatim from the contract
    document: Optional[str] = None      # e.g. fixtures/docs/…pdf
    page: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {"consent_id": self.consent_id, "licensee": self.licensee,
                "text": self.text, "document": self.document, "page": self.page}

    def describe(self) -> str:
        where = f"{self.document} p.{self.page}" if self.document else "the grant on file"
        return f"{self.licensee} ({self.consent_id}), {where}"


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything the notice may draw on, and nothing else.

    Frozen, and the grounding check compares the draft against exactly this —
    so a fact that is not here cannot appear in a letter that goes out.
    """

    finding_id: str
    performer_name: str
    url: str
    verdict: Verdict
    check: str
    reasoning: str
    evidence_quote: Optional[str] = None
    snapshot_uris: tuple[str, ...] = ()
    snapshot_sha256: tuple[str, ...] = ()
    captured_at: Optional[datetime] = None
    clause: Optional[ClauseCitation] = None
    territories_outside: tuple[str, ...] = ()
    modality: Optional[str] = None
    actor: Optional[str] = None
    injection_markers: tuple[str, ...] = ()

    # ------------------------------------------------------------------

    @property
    def allowed_urls(self) -> tuple[str, ...]:
        """Every URL a draft may mention: the listing and our own snapshots."""
        return tuple({self.url, *self.snapshot_uris})

    @property
    def quotable(self) -> tuple[str, ...]:
        """Every span a draft may present as a quotation."""
        spans = [self.evidence_quote, self.clause.text if self.clause else None]
        return tuple(s for s in spans if s and s.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "performer_name": self.performer_name,
            "url": self.url,
            "verdict": self.verdict.value,
            "check": self.check,
            "reasoning": self.reasoning,
            "evidence_quote": self.evidence_quote,
            "snapshot_uris": list(self.snapshot_uris),
            "snapshot_sha256": list(self.snapshot_sha256),
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "clause": self.clause.as_dict() if self.clause else None,
            "territories_outside": list(self.territories_outside),
            "modality": self.modality,
            "actor": self.actor,
            "injection_markers": list(self.injection_markers),
        }


def build_bundle(finding: Finding, performer: Performer,
                 verdict: VerdictResult, *,
                 snapshot: Optional[SnapshotRef] = None,
                 consents: Sequence[Consent] = (),
                 clause_text: Optional[str] = None,
                 injection_markers: Sequence[str] = ()) -> EvidenceBundle:
    """FR-5.2. Deterministic assembly — no model touches this.

    The clause is looked up from the consent the verdict actually named, not
    from "the performer's contracts" generally: the case file has to point at
    the one sentence it relies on.
    """
    consent_id = verdict.breached_consent_id or verdict.matched_consent_id
    clause: Optional[ClauseCitation] = None
    if consent_id:
        consent = next((c for c in consents if c.id == consent_id), None)
        if consent is not None:
            citation = _first_citation(consent, clause_text)
            clause = ClauseCitation(
                consent_id=consent.id, licensee=consent.licensee,
                text=citation.get("text"), document=consent.source_doc_ref,
                page=citation.get("page"))
        else:
            # The verdict named a grant we were not handed. Say so rather than
            # inventing a clause.
            clause = ClauseCitation(consent_id=consent_id, licensee="unknown")

    uris = tuple(u for u in (snapshot.text_uri if snapshot else None,
                             snapshot.screenshot_uri if snapshot else None,
                             snapshot.metadata_uri if snapshot else None) if u)
    hashes = tuple(h for h in (snapshot.text_sha256 if snapshot else None,
                               snapshot.screenshot_sha256 if snapshot else None)
                   if h)

    return EvidenceBundle(
        finding_id=finding.id, performer_name=performer.name, url=finding.url,
        verdict=verdict.verdict, check=verdict.check, reasoning=verdict.reason,
        evidence_quote=verdict.citation or finding.evidence_quote,
        snapshot_uris=uris, snapshot_sha256=hashes,
        captured_at=snapshot.captured_at if snapshot else None,
        clause=clause,
        territories_outside=tuple(verdict.territories_outside),
        modality=(finding.modality.value
                  if getattr(finding.modality, "value", None) else finding.modality),
        actor=None,
        injection_markers=tuple(injection_markers),
    )


def _first_citation(consent: Consent,
                    override: Optional[str]) -> dict[str, Any]:
    if override:
        return {"text": override, "page": None}
    for citation in consent.clause_citations or []:
        if not isinstance(citation, dict):
            continue
        # Either key. Everything that *writes* a citation writes `quote` —
        # `fixtures/seed.json`, `consent_ingest.to_consent`, the registry
        # screen — and this looked only for `text`, so the clause in every real
        # case file came out empty. Nothing failed; the section just rendered
        # "no clause to quote" under a verdict that was entirely about one
        # clause. Reading both is the small fix; the alternative is renaming a
        # field in the frozen contract.
        quote = citation.get("quote") or citation.get("text")
        if quote:
            return {"text": quote, "page": citation.get("page")}
    return {}


# --------------------------------------------------------------------------
# The grounding check — run before anyone reads the draft
# --------------------------------------------------------------------------

def ungrounded_facts(draft: str, bundle: EvidenceBundle) -> list[str]:
    """Every URL or quoted span in the draft that the bundle does not contain.

    Deliberately narrow: it checks the two things a model fabricates in a legal
    letter and that a reader will treat as verified — a link and a quotation.
    It is not a general-purpose fact checker and does not pretend to be.
    """
    problems: list[str] = []

    allowed = {_normalise_url(u) for u in bundle.allowed_urls}
    for found in _URL_RE.findall(draft or ""):
        if _normalise_url(found) not in allowed:
            problems.append(f"URL not in the evidence bundle: {found}")

    haystacks = [_squash(q) for q in bundle.quotable]
    haystacks.append(_squash(bundle.reasoning))
    for span in _QUOTE_RE.findall(draft or ""):
        squashed = _squash(span)
        if len(squashed) < MIN_QUOTED_CHARS:
            continue
        if not any(squashed in hay for hay in haystacks):
            problems.append(f"quoted text not in the evidence bundle: \"{span}\"")

    return problems


def _normalise_url(url: str) -> str:
    return (url or "").strip().rstrip(".,;:)”\"'").lower()


def _squash(text: str) -> str:
    return " ".join((text or "").split()).lower()


# --------------------------------------------------------------------------
# The draft
# --------------------------------------------------------------------------

@dataclass
class Dossier:
    """A case file: the bundle, the draft, and how the draft was checked."""

    bundle: EvidenceBundle
    draft: Optional[str] = None
    ok: bool = False
    reason: Optional[str] = None
    grounding_failures: tuple[str, ...] = ()
    repairs: int = 0
    from_cache: bool = False
    model: Optional[str] = None

    @property
    def sendable(self) -> bool:
        """Always false, and it always will be.

        Kept as a named property so any UI asking "can I send this?" gets a
        straight no from the domain object rather than from a missing button
        somebody might add. FR-5.5.
        """
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.bundle.finding_id,
            "ok": self.ok,
            "reason": self.reason,
            "grounding_failures": list(self.grounding_failures),
            "repairs": self.repairs,
            "draft_chars": len(self.draft or ""),
            "has_clause": bool(self.bundle.clause),
            "clause_id": (self.bundle.clause.consent_id
                          if self.bundle.clause else None),
            "snapshots": len(self.bundle.snapshot_uris),
            "from_cache": self.from_cache,
            "model": self.model,
            "sendable": self.sendable,
        }


Generate = Callable[[str, str], str]


@dataclass
class DossierWriter:
    deps: HarnessDeps = field(default_factory=HarnessDeps)
    generate: Optional[Generate] = None
    model: str = DEFAULT_MODEL
    temperature: float = TEMPERATURE
    timeout_s: float = 60.0
    max_attempts: int = 2
    sleep: Callable[[float], None] = time.sleep
    _harness: Optional[Harness] = field(default=None, repr=False, compare=False)

    @property
    def policy(self) -> HarnessPolicy:
        return HarnessPolicy(
            agent_name=AGENT_NAME,
            # A notice we could not draft leaves a finding a human must handle.
            fail_state=FailState.AMBIGUOUS,
            timeout_s=self.timeout_s,
            max_attempts=self.max_attempts,
            max_repairs=1,                  # WU-16: reject and regenerate once
            tools=(),                       # nothing to act with, by construction
            temperature=self.temperature,
            cache="none",                   # a draft follows a verdict; never cache it
            armor_response=ARMOR_TEMPLATE_OUT,
        )

    @property
    def harness(self) -> Harness:
        if self._harness is None:
            self._harness = Harness(self.policy, self.deps, sleep=self.sleep)
        return self._harness

    def _generator(self) -> Generate:
        if self.generate is None:
            self.generate = adk_generator(self.model, self.temperature)
        return self.generate

    # ------------------------------------------------------------------

    def write(self, bundle: EvidenceBundle) -> Dossier:
        """Draft a notice for one bundle. Never raises.

        Refuses outright for anything but an `unauthorized` verdict: drafting a
        takedown for an `ambiguous` finding would put the doubt in an envelope.
        """
        if bundle.verdict is not Verdict.UNAUTHORIZED:
            return self._refuse(
                bundle, f"no notice is drafted for a {bundle.verdict.value} "
                        "finding; only an unauthorised one has something to say")
        if not (bundle.evidence_quote or "").strip():
            return self._refuse(
                bundle, "no evidence quote in the bundle, so there is nothing "
                        "to cite and no notice to draft")

        instruction = build_instruction()
        failures: list[str] = []

        def invoke(repair_hint: Optional[str]) -> str:
            prompt = build_prompt(bundle)
            if repair_hint:
                prompt = (f"{prompt}\n\nYour previous draft was rejected:\n"
                          f"{repair_hint}\nRewrite it using ONLY the facts "
                          f"above. Remove anything not in the bundle.")
            text = (self._generator()(instruction, prompt) or "").strip()
            if not text:
                raise ValidationError(f"{AGENT_NAME}: the model returned nothing")

            problems = ungrounded_facts(text, bundle)
            if problems:
                failures.clear()
                failures.extend(problems)
                raise UngroundedDraft("; ".join(problems))
            return text

        result = self.harness.run(invoke, subject_id=bundle.finding_id,
                                  response_text=lambda text: text,
                                  provider="gemini")

        dossier = Dossier(
            bundle=bundle, draft=result.value if result.ok else None,
            ok=result.ok, reason=None if result.ok else result.reason,
            grounding_failures=tuple(failures) if not result.ok else (),
            repairs=result.repairs, from_cache=result.from_cache,
            model=self.model)
        self._record(dossier, result.duration_s)
        return dossier

    def for_finding(self, finding: Finding, performer: Performer,
                    verdict: VerdictResult, *,
                    snapshot: Optional[SnapshotRef] = None,
                    consents: Sequence[Consent] = (),
                    injection_markers: Sequence[str] = ()) -> Dossier:
        """Bundle and draft in one call, for the enforcement pipeline."""
        return self.write(build_bundle(
            finding, performer, verdict, snapshot=snapshot, consents=consents,
            injection_markers=injection_markers))

    # ------------------------------------------------------------------

    def _refuse(self, bundle: EvidenceBundle, reason: str) -> Dossier:
        dossier = Dossier(bundle=bundle, ok=False, reason=reason)
        log.info("%s finding=%s refused: %s", AGENT_NAME, bundle.finding_id,
                 reason)
        self._record(dossier, 0.0)
        return dossier

    def _record(self, dossier: Dossier, duration_s: float) -> None:
        """The trail. Records that a draft exists and how it was checked —
        never the draft text, which quotes an attacker-controlled page."""
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": AGENT_NAME,
            "event": "dossier",
            "subject_id": dossier.bundle.finding_id,
            "url": dossier.bundle.url,
            "verdict": dossier.bundle.verdict.value,
            "check": dossier.bundle.check,
            "temperature": self.temperature,
            "latency_s": round(duration_s, 4),
        }
        event.update(dossier.as_dict())
        self.deps.audit.append(event)

        line = (f"{AGENT_NAME} finding={dossier.bundle.finding_id} "
                f"ok={dossier.ok} chars={len(dossier.draft or '')} "
                f"clause={dossier.bundle.clause.consent_id if dossier.bundle.clause else '-'} "
                f"snapshots={len(dossier.bundle.snapshot_uris)} "
                f"repairs={dossier.repairs} sendable=False")
        if dossier.ok:
            log.info(line)
        else:
            log.warning("%s reason=%r ungrounded=%s", line, dossier.reason,
                        list(dossier.grounding_failures))


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------

def build_instruction() -> str:
    """What the model may write, and the two things it may never do."""
    return """You draft a factual notice about a listing that appears to use a performer's
voice or likeness outside the terms of a consent agreement. You write the
letter; you do not decide anything and you do not add facts.

USE ONLY THE BUNDLE
Every fact in your draft must come from the EVIDENCE BUNDLE below. Do not add
a URL that is not in the bundle. Do not quote a sentence that is not in the
bundle. Do not invent dates, prices, case numbers, statutes or deadlines. A
draft containing anything not in the bundle is rejected automatically.

WHAT THE NOTICE SAYS
- who the performer is, and what the listing appears to offer
- the quoted sentence from the page that shows it
- the specific grant it falls outside, named by its licensee and id, and the
  clause text if the bundle has one
- exactly which territories are outside the grant, if any
- that a snapshot of the page was preserved at discovery, and its hash
- a request that the recipient review and respond

WHAT THE NOTICE MUST NOT DO
- **No legal conclusions.** Do not write that this constitutes infringement,
  violates any statute, or breaches any law. State the evidence and the
  mismatch with the grant, and stop. We are not lawyers and the notice must
  not pretend otherwise.
- **No legal advice**, no demands for damages, no deadlines, no threats.
- Do not say the notice has been sent, filed or served. It is a draft a human
  will review.

STYLE
Plain professional English. Short paragraphs. No letterhead, no signature
block, no placeholders in square brackets. 200 to 350 words. Return the letter
text only."""


def build_prompt(bundle: EvidenceBundle) -> str:
    """The bundle as the model sees it: labelled facts, nothing else."""
    lines = [
        "EVIDENCE BUNDLE",
        f"performer: {bundle.performer_name}",
        f"listing_url: {bundle.url}",
        f"modality: {bundle.modality or 'unspecified'}",
        f"finding_id: {bundle.finding_id}",
        f"verdict: {bundle.verdict.value} (rule check: {bundle.check})",
        f"reasoning: {bundle.reasoning}",
        f"quoted_from_the_page: \"{bundle.evidence_quote}\"",
    ]
    if bundle.territories_outside:
        lines.append("territories_outside_the_grant: "
                     + ", ".join(bundle.territories_outside))
    if bundle.clause:
        lines.append(f"grant: {bundle.clause.describe()}")
        if bundle.clause.text:
            lines.append(f"clause_text: \"{bundle.clause.text}\"")
    for uri, digest in zip(bundle.snapshot_uris,
                           list(bundle.snapshot_sha256) + [""] * 3):
        lines.append(f"snapshot: {uri}" + (f" (sha256 {digest})" if digest else ""))
    if bundle.captured_at:
        lines.append(f"captured_at: {bundle.captured_at.isoformat()}")
    if bundle.injection_markers:
        lines.append("note_for_the_reviewer_only: the page also contained text "
                     "addressed at an automated system ("
                     + ", ".join(bundle.injection_markers)
                     + "). Do not mention this in the notice.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# ADK wiring
# --------------------------------------------------------------------------

def build_agent(model: str = DEFAULT_MODEL,
                temperature: float = TEMPERATURE, *,
                instruction: Optional[str] = None) -> Any:
    """An `LlmAgent` with **no tools**.

    The notice is free-form text, so unlike the extractors there is no
    `output_schema` here — which makes `tools=()` the load-bearing part. There
    is nothing for it to call, so there is nothing for it to send.
    """
    from google.adk.agents import LlmAgent
    from google.genai import types

    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        instruction=instruction or build_instruction(),
        tools=[],
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=temperature),
    )


def adk_generator(model: str = DEFAULT_MODEL,
                  temperature: float = TEMPERATURE) -> Generate:
    from google.genai import types

    def generate(instruction: str, payload: str) -> str:
        from google.adk.runners import InMemoryRunner

        agent = build_agent(model, temperature, instruction=instruction)
        runner = InMemoryRunner(agent=agent, app_name="consentinel")
        session = _sync(runner.session_service.create_session(
            app_name="consentinel", user_id=AGENT_NAME))
        text = ""
        for event in runner.run(
            user_id=AGENT_NAME, session_id=session.id,
            new_message=types.Content(role="user",
                                      parts=[types.Part(text=payload)]),
        ):
            for part in (event.content.parts if event.content else []) or []:
                if getattr(part, "text", None):
                    text = part.text
        return text

    return generate


def _sync(coro: Any) -> Any:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
