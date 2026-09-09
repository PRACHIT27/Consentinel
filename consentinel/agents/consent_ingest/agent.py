"""WU-03 — read a signed contract and fill in a permission slip.

A person uploads a PDF. Gemini pulls out the six things that define what a
studio may do with an AI copy of a performer, and for each one it must quote the
sentence it got the answer from, with the page number.

The quote is the whole point. A lawyer will not trust an extraction they cannot
check in two seconds, and a citation that cannot be found on the page it claims
is worse than no citation at all. Every quote is checked against the real page
text before the draft comes back, and a field whose quote does not check out is
dropped rather than quietly repaired.

Nothing here writes to the database. It returns a draft; a person confirms it
and the web layer saves it. Every verdict the product ever produces resolves
against that record, so it is the one place worth a human gate.

The prompt lives in `instructions.py` and the checks live in `guardrail.py`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from consentinel.agents.common.gemini import generate_json
from consentinel.agents.consent_ingest.guardrail import (
    check_citations_are_real,
    check_vocabulary,
    norm,
)
from consentinel.agents.consent_ingest.instructions import (
    INSTRUCTION,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    USES,
)
from consentinel.harness.policy import FailState, HarnessPolicy
from consentinel.harness.ports import HarnessDeps
from consentinel.harness.result import HarnessResult
from consentinel.harness.runner import Harness
from consentinel.model_armor import INGEST_IN
from consentinel.store.base import Consent, PermittedUse

POLICY = HarnessPolicy(
    agent_name="consent_ingest",
    fail_state=FailState.UNVERIFIED,
    tools=(),                            # reads a document, calls nothing
    output_schema=dict,
    temperature=0.0,
    cache="content",                     # a PDF never changes, so its hash is the key
    # User-supplied text, so it is screened for personal data on the way in.
    #
    # The constant, not a literal. This read `"consentinel-ingest"`, and the
    # template `infra/model_armor/01_templates.sh` creates is
    # `consentinel-ingest-in` — so the moment the app began passing a real
    # `ModelArmor`, every upload would have resolved `armor_unavailable`
    # against a template that does not exist, and the only sign would have been
    # one log line. `model_armor` owns these names; nobody should retype them.
    armor_prompt=INGEST_IN,
    timeout_s=90.0,
)


@dataclass
class ConsentDraft:
    """What comes back for a person to confirm. Not saved until they do."""

    performer_name: str = ""
    licensee: str = ""
    permitted_uses: list[str] = field(default_factory=list)
    territories: list[str] = field(default_factory=list)
    valid_from: str = ""
    valid_to: str = ""
    compensation_trigger: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    source_doc_ref: Optional[str] = None
    page_count: int = 0

    def citation_for(self, field_name: str) -> Optional[dict[str, Any]]:
        return next((c for c in self.citations if c.get("field") == field_name), None)


def read_pages(pdf: Path) -> list[str]:
    """One string per page, in order. Citation pages are 1-based, so page N is
    `pages[N - 1]`."""
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(pdf)).pages]


def cache_key(pdf: Path) -> str:
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return f"consent_ingest:{digest}:{PROMPT_VERSION}"


def extract_consent(
    pdf: Path,
    *,
    deps: Optional[HarnessDeps] = None,
    generate: Optional[Any] = None,
) -> HarnessResult:
    """Read `pdf` and return a `ConsentDraft` inside a HarnessResult.

    `generate(instruction, document_text, schema)` does the model call. Injected
    so tests run without the network; defaults to Gemini.
    """
    pages = read_pages(pdf)
    if not any(norm(p) for p in pages):
        return HarnessResult(
            ok=False,
            fail_state=POLICY.fail_state,
            reason="no readable text in the PDF - it may be a scan, which needs OCR first",
            agent=POLICY.agent_name,
        )

    document = "\n\n".join(f"--- page {i + 1} ---\n{p}" for i, p in enumerate(pages))
    instruction = INSTRUCTION.format(uses=", ".join(USES))

    def call(inst: str, doc: str, schema: dict[str, Any]) -> dict[str, Any]:
        return generate_json(inst, doc, schema, label="CONTRACT TEXT",
                             temperature=POLICY.temperature)

    do_generate = generate or call

    def invoke(repair_hint: Optional[str]) -> dict[str, Any]:
        prompt = instruction
        if repair_hint:
            # The one repair attempt. Say exactly what failed — "try again"
            # produces the same answer.
            prompt += (
                "\n\nA previous attempt was rejected. Fix these and return the whole object again:\n"
                f"{repair_hint}\n"
                "If you cannot find a sentence that supports a field, leave that field empty."
            )
        return do_generate(prompt, document, RESPONSE_SCHEMA)

    harness = Harness(POLICY, deps or HarnessDeps(prompt_version=PROMPT_VERSION))
    result = harness.run(
        invoke,
        subject_id=pdf.name,
        cache_key=cache_key(pdf),
        untrusted_text=document,
        validators=(check_citations_are_real(pages), check_vocabulary),
    )

    if not result.ok:
        return result

    return HarnessResult(
        ok=True,
        value=_to_draft(result.value, pages, pdf),
        agent=result.agent,
        prompt_version=result.prompt_version,
        from_cache=result.from_cache,
        cache_age_s=result.cache_age_s,
        attempts=result.attempts,
        repairs=result.repairs,
        duration_s=result.duration_s,
        armor_findings=result.armor_findings,
        injection_suspected=result.injection_suspected,
    )


def _to_draft(payload: dict[str, Any], pages: list[str], pdf: Path) -> ConsentDraft:
    """Shape the answer into a draft, dropping anything uncited.

    An uncited field would be a permission record nobody can verify, which is
    the exact thing this step exists to prevent.
    """
    cited = {c["field"] for c in payload.get("citations", [])}
    draft = ConsentDraft(
        citations=list(payload.get("citations", [])),
        source_doc_ref=str(pdf),
        page_count=len(pages),
    )

    for name in ("performer_name", "licensee", "valid_from", "valid_to", "compensation_trigger"):
        value = payload.get(name) or ""
        if value and name not in cited:
            draft.dropped.append({"field": name, "why": "no citation"})
            continue
        setattr(draft, name, value)

    for name in ("permitted_uses", "territories"):
        value = list(payload.get(name) or [])
        if value and name not in cited:
            draft.dropped.append({"field": name, "why": "no citation"})
            continue
        setattr(draft, name, value)

    return draft


def to_consent(draft: ConsentDraft, *, consent_id: str, performer_id: str) -> Consent:
    """Turn a confirmed draft into the record we store. Called by the web layer
    after a person has checked it, never from here."""

    def _date(value: str) -> Optional[datetime]:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if value else None

    return Consent(
        id=consent_id,
        performer_id=performer_id,
        licensee=draft.licensee,
        permitted_uses=[PermittedUse(u) for u in draft.permitted_uses],
        territories=list(draft.territories),
        valid_from=_date(draft.valid_from),
        valid_to=_date(draft.valid_to),
        compensation_trigger=draft.compensation_trigger or None,
        clause_citations=[{"quote": c["quote"], "page": c["page"]} for c in draft.citations],
        source_doc_ref=draft.source_doc_ref,
    )


# --------------------------------------------------------------------------
# ADK wiring
# --------------------------------------------------------------------------

def build_agent(model: Optional[str] = None, *, instruction: Optional[str] = None) -> Any:
    """The ADK `LlmAgent` for the contract read — WU-24 deploys this.

    With `output_schema` set, ADK refuses tools and agent transfer outright — so
    "no free-form output channel, no capability" becomes a property of the
    runtime rather than a promise in a prompt. That matters here: this agent
    reads a document someone else wrote.

    `ConsentOut` mirrors `instructions.RESPONSE_SCHEMA`, which is what the app's
    own Gemini call uses. A test asserts the two stay in step.
    """
    from google.adk.agents import LlmAgent
    from google.genai import types

    from consentinel.agents.consent_ingest.instructions import ConsentOut

    return LlmAgent(
        name="ConsentIngest",
        model=model or os.environ.get("CONSENTINEL_MODEL", "gemini-2.5-flash"),
        instruction=instruction or INSTRUCTION.format(uses=", ".join(USES)),
        output_schema=ConsentOut,
        output_key="permission_slip",
        include_contents="none",       # one contract, one reading, no history
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=POLICY.temperature,
        ),
    )
