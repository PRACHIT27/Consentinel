"""WU-15 (capture) and WU-16 acceptance.

Done when: the case file names the specific clause it relies on, and **no send
path exists anywhere in the codebase**. Both are asserted here — the second by
walking every source file in the repo, because "we did not add an email client"
is a claim that has to stay true after the next twenty commits.

The other test worth reading is the grounding one. A generated legal-ish letter
is exactly where a model invents a statute, a deadline or a URL, so the draft is
checked against the bundle rather than trusted.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from consentinel.agents.dossier_writer import (
    ARMOR_TEMPLATE_OUT,
    DossierWriter,
    EvidenceBundle,
    build_bundle,
    build_instruction,
    build_prompt,
    ungrounded_facts,
)
from consentinel.agents.reconciler import Observation, evaluate
from consentinel.agents.snapshot import (
    LocalSnapshotStore,
    SnapshotCapture,
    SnapshotRef,
)
from consentinel.harness import HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.store.base import (
    Consent,
    DiscoveredVia,
    Finding,
    FindingStatus,
    Modality,
    Performer,
    PermittedUse,
    Verdict,
)
from consentinel.tools.contracts import PageSnapshot

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
URL = "https://vozclone.example/mira"
QUOTE = "Compre o clone de voz de Mira Vance por R$ 49,90."
CLAUSE = ("Producer may generate synthetic voice performances of the Artist "
          "solely for the Picture, in the United States and Canada.")

MIRA = Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])

GRANT = Consent(
    id="c_aurora", performer_id="perf_mira", licensee="Aurora Studios",
    permitted_uses=[PermittedUse.VOICE_SYNTH], territories=["US", "CA"],
    clause_citations=[{"text": CLAUSE, "page": 2}],
    source_doc_ref="fixtures/docs/mira_vance_halcyon_agreement.pdf")

FINDING = Finding(
    id="find_1", performer_id="perf_mira", url=URL, url_hash="abc123",
    discovered_via=DiscoveredVia.TEXT, discovered_locale="pt-BR",
    modality=Modality.VOICE, evidence_quote=QUOTE, confidence=0.93)

PAGE = PageSnapshot(url=URL, text=f"Clone de voz IA\n{QUOTE}\nEntrega no Brasil.",
                    media_refs=["https://cdn.example/demo.mp3"], fetched_at=NOW)


def verdict():
    return evaluate(Observation(
        performer_id="perf_mira", modality="voice",
        target_territories=("BR",), actor="VozClone Studio", confidence=0.93,
        evidence_quote=QUOTE), [GRANT], NOW)


def snapshot(tmp_path: Path, audit: Optional[MemoryAudit] = None) -> SnapshotRef:
    capture = SnapshotCapture(store=LocalSnapshotStore(root=tmp_path),
                              audit=audit)
    return capture.capture("find_1", PAGE)


def bundle(tmp_path: Optional[Path] = None) -> EvidenceBundle:
    ref = snapshot(tmp_path) if tmp_path else None
    return build_bundle(FINDING, MIRA, verdict(), snapshot=ref,
                        consents=[GRANT])


def good_draft(b: EvidenceBundle) -> str:
    """What a well-behaved model returns: only facts from the bundle."""
    return (
        f"Regarding the listing at {b.url}\n\n"
        f"This listing appears to offer an AI-generated voice of the performer "
        f"{b.performer_name}. The page states: \"{QUOTE}\"\n\n"
        f"Our records hold a consent agreement with Aurora Studios "
        f"(c_aurora) covering synthetic voice use in the United States and "
        f"Canada. The clause reads: \"{CLAUSE}\"\n\n"
        f"This offering appears directed at BR, which falls outside that "
        f"grant. A snapshot of the page was preserved at discovery.\n\n"
        f"We ask that you review this listing and respond."
    )


class FakeModel:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, instruction: str, payload: str) -> str:
        self.calls.append((instruction, payload))
        if not self.answers:
            return ""
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


def writer(*answers: str) -> tuple[DossierWriter, FakeModel, MemoryAudit]:
    model = FakeModel(*answers)
    audit = MemoryAudit()
    w = DossierWriter(deps=HarnessDeps(audit=audit, metrics=MemoryMetrics()),
                      generate=model, sleep=lambda _s: None)
    return w, model, audit


# ------------------------------------------------ WU-15, the capture half

def test_a_snapshot_is_written_with_text_metadata_and_hashes(tmp_path):
    store = LocalSnapshotStore(root=tmp_path)
    audit = MemoryAudit()

    ref = SnapshotCapture(store=store, audit=audit).capture("find_1", PAGE)

    assert ref.text_uri and ref.metadata_uri
    assert ref.text_sha256 and len(ref.text_sha256) == 64
    assert ref.text_chars == len(PAGE.text)
    assert store.read(ref.text_uri).decode("utf-8") == PAGE.text
    metadata = json.loads(store.read(ref.metadata_uri).decode("utf-8"))
    assert metadata["url"] == URL
    assert metadata["text"]["sha256"] == ref.text_sha256
    assert metadata["media_refs"] == ["https://cdn.example/demo.mp3"]
    assert audit.events[-1]["event"] == "snapshot"


def test_the_snapshot_survives_the_page_being_taken_down(tmp_path):
    """WU-15's acceptance line. The page will disappear — that is the point of
    a takedown — and the evidence must not disappear with it."""
    ref = snapshot(tmp_path)
    store = LocalSnapshotStore(root=tmp_path)

    # the page is gone; nothing about our copy changes
    assert store.read(ref.text_uri).decode("utf-8") == PAGE.text
    assert QUOTE in store.read(ref.text_uri).decode("utf-8")


def test_evidence_is_never_overwritten(tmp_path):
    """An evidence object that can be replaced is not evidence."""
    capture = SnapshotCapture(store=LocalSnapshotStore(root=tmp_path))

    first = capture.capture("find_1", PAGE)
    second = capture.capture("find_1", PageSnapshot(url=URL, text="different"))

    assert first.text_uri != second.text_uri
    assert LocalSnapshotStore(root=tmp_path).read(first.text_uri) \
        .decode("utf-8") == PAGE.text


def test_the_store_exposes_no_delete(tmp_path):
    """Hard rule 9, and the ABC omits it too — removing the capability beats
    remembering not to use it."""
    from consentinel.store.base import EvidenceStore

    store = LocalSnapshotStore(root=tmp_path)

    for forbidden in ("delete", "remove", "purge", "unlink", "expire"):
        assert not hasattr(store, forbidden), forbidden
        assert not hasattr(EvidenceStore, forbidden), forbidden


def test_unlawful_material_is_recorded_but_never_stored(tmp_path):
    """DESIGN Part III §4: the one place we deliberately keep less. Nothing is
    snapshotted, nothing is rendered; the address, a hash, the classification
    and the time are kept, and a person is told."""
    store = LocalSnapshotStore(root=tmp_path)
    audit = MemoryAudit()

    ref = SnapshotCapture(store=store, audit=audit).capture(
        "find_2", PAGE, unlawful=True, classification="csam_suspected")

    assert ref.withheld is True
    assert ref.status is FindingStatus.ESCALATED_UNLAWFUL
    assert ref.text_uri is None and ref.screenshot_uri is None
    assert ref.text_sha256                      # still identifiable
    assert list(tmp_path.iterdir()) == []       # nothing on disk at all
    assert audit.events[-1]["withheld_reason"] == "csam_suspected"


def test_a_screenshot_is_captured_when_one_is_available(tmp_path):
    store = LocalSnapshotStore(root=tmp_path)
    capture = SnapshotCapture(store=store,
                              screenshot=lambda _url: b"\x89PNG fake")

    ref = capture.capture("find_1", PAGE)

    assert ref.screenshot_uri and ref.screenshot_sha256
    assert store.read(ref.screenshot_uri) == b"\x89PNG fake"


def test_text_only_is_a_shipped_answer_not_a_failure(tmp_path):
    """WU-15 permits text-only if Chromium turns into a rabbit hole (OQ-4)."""
    ref = SnapshotCapture(store=LocalSnapshotStore(root=tmp_path)).capture(
        "find_1", PAGE)

    assert ref.screenshot_uri is None
    assert ref.text_uri                       # the evidence that matters
    assert ref.withheld is False


def test_a_broken_screenshot_does_not_lose_the_text(tmp_path):
    def explode(_url: str) -> bytes:
        raise RuntimeError("chromium not installed")

    ref = SnapshotCapture(store=LocalSnapshotStore(root=tmp_path),
                          screenshot=explode).capture("find_1", PAGE)

    assert ref.text_uri and ref.screenshot_uri is None


def test_the_finding_gets_one_evidence_uri(tmp_path):
    ref = snapshot(tmp_path)

    assert ref.evidence_uri == ref.text_uri


# ------------------------------------------------ WU-16, the bundle (FR-5.2)

def test_the_bundle_names_the_specific_clause_it_relies_on(tmp_path):
    """FR-5's acceptance: a case file that says "you are outside the terms"
    without pointing at the sentence is one the recipient can dismiss."""
    b = bundle(tmp_path)

    assert b.clause is not None
    assert b.clause.consent_id == "c_aurora"
    assert b.clause.licensee == "Aurora Studios"
    assert b.clause.text == CLAUSE
    assert b.clause.page == 2
    assert b.clause.document.endswith("mira_vance_halcyon_agreement.pdf")
    assert "p.2" in b.clause.describe()


def test_the_bundle_carries_the_url_snapshots_quote_and_reasoning(tmp_path):
    b = bundle(tmp_path)

    assert b.url == URL
    assert b.evidence_quote == QUOTE
    assert len(b.snapshot_uris) == 2          # text + metadata, no screenshot
    assert b.snapshot_sha256
    assert b.territories_outside == ("BR",)
    assert b.verdict is Verdict.UNAUTHORIZED
    assert "BR" in b.reasoning


def test_a_verdict_naming_a_grant_we_were_not_handed_says_unknown():
    """Rather than inventing a clause for it."""
    b = build_bundle(FINDING, MIRA, verdict(), consents=[])

    assert b.clause is not None
    assert b.clause.consent_id == "c_aurora"
    assert b.clause.licensee == "unknown"
    assert b.clause.text is None


def test_the_allowed_urls_are_the_listing_and_our_own_snapshots(tmp_path):
    b = bundle(tmp_path)

    assert URL in b.allowed_urls
    for uri in b.snapshot_uris:
        assert uri in b.allowed_urls
    assert len(b.allowed_urls) == 3


# ------------------------------------------- WU-16, the grounding check

def test_a_well_grounded_draft_passes(tmp_path):
    b = bundle(tmp_path)

    assert ungrounded_facts(good_draft(b), b) == []


def test_an_invented_url_is_caught(tmp_path):
    b = bundle(tmp_path)
    draft = good_draft(b) + "\n\nSee also https://legal.example/notices/1234"

    problems = ungrounded_facts(draft, b)

    assert len(problems) == 1
    assert "legal.example" in problems[0]


def test_an_invented_quotation_is_caught(tmp_path):
    b = bundle(tmp_path)
    draft = good_draft(b).replace(
        f"\"{QUOTE}\"", "\"This voice model was licensed for worldwide use.\"")

    problems = ungrounded_facts(draft, b)

    assert any("quoted text not in the evidence bundle" in p for p in problems)


def test_ordinary_short_phrases_in_quotes_are_not_treated_as_citations(tmp_path):
    """Otherwise every honest letter gets rejected for saying "no consent"."""
    b = bundle(tmp_path)
    draft = good_draft(b) + "\n\nThe listing is marked \"instant\"."

    assert ungrounded_facts(draft, b) == []


def test_the_check_is_not_fooled_by_trailing_punctuation(tmp_path):
    b = bundle(tmp_path)
    draft = f"Regarding {b.url}. The page states: \"{QUOTE}\""

    assert ungrounded_facts(draft, b) == []


# ------------------------------------------------ WU-16, drafting

def test_a_grounded_draft_is_returned(tmp_path):
    b = bundle(tmp_path)
    w, model, _ = writer(good_draft(b))

    dossier = w.write(b)

    assert dossier.ok is True
    assert dossier.draft and QUOTE in dossier.draft
    assert "c_aurora" in dossier.draft
    assert dossier.repairs == 0
    assert len(model.calls) == 1


def test_an_ungrounded_draft_is_rejected_and_regenerated_once(tmp_path):
    """WU-16: reject and regenerate once. The model gets to phrase the letter;
    it does not get to add facts."""
    b = bundle(tmp_path)
    invented = good_draft(b) + "\n\nFiled under case 4:26-cv-00931."
    invented = invented.replace(
        "We ask that you review",
        "Per 17 U.S.C. §106 you must respond within 48 hours. "
        "See https://courts.example/case/4-26-cv-00931 . We ask that you review")
    w, model, _ = writer(invented, good_draft(b))

    dossier = w.write(b)

    assert dossier.ok is True
    assert dossier.repairs == 1
    assert len(model.calls) == 2
    assert "courts.example" in model.calls[1][1]      # the hint named the fact
    assert "rejected" in model.calls[1][1]


def test_a_model_that_keeps_inventing_gets_no_draft_at_all(tmp_path):
    b = bundle(tmp_path)
    invented = good_draft(b) + "\n\nSee https://invented.example/notice"
    w, _, _ = writer(invented, invented)

    dossier = w.write(b)

    assert dossier.ok is False
    assert dossier.draft is None
    assert dossier.grounding_failures
    assert "invented.example" in dossier.grounding_failures[0]


def test_no_notice_is_drafted_for_an_ambiguous_finding(tmp_path):
    """Drafting a takedown for a finding we could not judge would put the doubt
    in an envelope."""
    ambiguous = evaluate(Observation(
        performer_id="perf_mira", modality="voice",
        target_territories=("BR",), actor="VozClone Studio", confidence=0.3,
        evidence_quote=QUOTE), [GRANT], NOW)
    b = build_bundle(FINDING, MIRA, ambiguous, consents=[GRANT])
    w, model, _ = writer(good_draft(bundle(tmp_path)))

    dossier = w.write(b)

    assert dossier.ok is False
    assert dossier.verdict_refusal if hasattr(dossier, "verdict_refusal") else True
    assert "ambiguous" in dossier.reason
    assert model.calls == []                  # no model call at all


def test_no_notice_is_drafted_without_a_quote_to_cite():
    b = EvidenceBundle(
        finding_id="find_1", performer_name="Mira Vance", url=URL,
        verdict=Verdict.UNAUTHORIZED, check="territory_outside_grant",
        reasoning="targets BR", evidence_quote=None)
    w, model, _ = writer("anything")

    dossier = w.write(b)

    assert dossier.ok is False
    assert "nothing to cite" in dossier.reason
    assert model.calls == []


def test_an_empty_model_response_is_a_failure_not_an_empty_notice(tmp_path):
    w, _, _ = writer("", "")

    dossier = w.write(bundle(tmp_path))

    assert dossier.ok is False
    assert dossier.draft is None


# ---------------------------------------------- WU-16, what it must not do

def test_the_instruction_forbids_legal_conclusions_and_advice():
    """PRD non-goal NG-4. We are not lawyers and the notice must not pretend
    otherwise."""
    instruction = build_instruction()

    assert "No legal conclusions" in instruction
    assert "constitutes infringement" in instruction
    assert "No legal advice" in instruction
    assert "no deadlines, no threats" in instruction
    assert "draft a human" in instruction or "draft a human will review" in instruction


def test_the_prompt_hands_over_the_bundle_and_nothing_else(tmp_path):
    b = bundle(tmp_path)

    prompt = build_prompt(b)

    assert URL in prompt
    assert QUOTE in prompt
    assert CLAUSE in prompt
    assert "c_aurora" in prompt
    assert "territories_outside_the_grant: BR" in prompt
    assert b.snapshot_sha256[0] in prompt


def test_an_injection_note_is_for_the_reviewer_not_the_notice(tmp_path):
    b = build_bundle(FINDING, MIRA, verdict(), consents=[GRANT],
                     injection_markers=["verdict_steering"])

    prompt = build_prompt(b)

    assert "note_for_the_reviewer_only" in prompt
    assert "Do not mention this in the notice" in prompt


def test_the_writer_holds_no_tools_and_screens_its_output(tmp_path):
    w, _, _ = writer(good_draft(bundle(tmp_path)))

    assert w.policy.tools == ()
    assert w.policy.armor_response == ARMOR_TEMPLATE_OUT   # block on the way out
    assert w.policy.cache == "none"                        # a draft follows a verdict
    assert w.policy.max_repairs == 1


def test_the_agent_has_no_tools_to_act_with():
    from consentinel.agents.dossier_writer import build_agent

    agent = build_agent(model="gemini-2.5-flash")

    assert not agent.tools
    assert agent.disallow_transfer_to_parent is True
    assert agent.generate_content_config.temperature == 0.3


def test_a_dossier_is_never_sendable(tmp_path):
    w, _, _ = writer(good_draft(bundle(tmp_path)))

    dossier = w.write(bundle(tmp_path))

    assert dossier.sendable is False
    assert dossier.as_dict()["sendable"] is False


def test_no_send_path_exists_anywhere_in_the_codebase():
    """WU-16's acceptance line, and the reason it is a repo-wide grep: "we did
    not add an email client" has to stay true after the next twenty commits.

    An agent that can act on a mistaken verdict against a third party is a
    liability, so the absence is structural (hard rule 6, FR-5.5).
    """
    root = Path(__file__).resolve().parent.parent
    forbidden = (
        "smtplib", "sendgrid", "mailgun", "postmark", "ses.send_email",
        "aiosmtplib", "send_message(", "send_email", "sendmail",
        "twilio", "slack_sdk", "webhook_url",
    )
    skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
            ".cache", "evidence", "node_modules"}

    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if set(path.parts) & skip or path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for needle in forbidden:
            if needle.lower() in text:
                offenders.append(f"{path.relative_to(root)}: {needle}")

    assert offenders == [], (
        "a send path appeared in the codebase; WU-16 and FR-5.5 forbid it: "
        + "; ".join(offenders))


def test_requirements_pull_in_no_mail_or_messaging_client():
    root = Path(__file__).resolve().parent.parent
    for name in ("requirements.txt", "requirements-dev.txt"):
        text = (root / name).read_text(encoding="utf-8").lower()
        for forbidden in ("sendgrid", "mailgun", "smtp", "twilio", "slack",
                          "postmark", "resend"):
            assert forbidden not in text, f"{name}: {forbidden}"


# ------------------------------------------------------------------ trail

def test_the_audit_row_records_the_draft_without_its_text(tmp_path):
    """The draft quotes an attacker-controlled page, so the trail records that
    a draft exists and how it was checked — not what it says."""
    b = bundle(tmp_path)
    w, _, audit = writer(good_draft(b))

    w.write(b)

    row = next(e for e in audit.events if e.get("event") == "dossier")
    assert row["ok"] is True
    assert row["clause_id"] == "c_aurora"
    assert row["snapshots"] == 2
    assert row["sendable"] is False
    assert QUOTE not in json.dumps(row, ensure_ascii=False)
    assert row["draft_chars"] > 200


def test_the_log_line_says_it_is_not_sendable(caplog, tmp_path):
    b = bundle(tmp_path)
    w, _, _ = writer(good_draft(b))

    with caplog.at_level(logging.INFO, logger="consentinel.dossier_writer"):
        w.write(b)

    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("DossierWriter "))
    assert "sendable=False" in line
    assert "clause=c_aurora" in line


# ------------------------------------------------------------ end to end

def test_the_enforcement_output_path_runs_from_finding_to_draft(tmp_path):
    """Snapshot, bundle, draft — the whole of epic 5 in one test."""
    audit = MemoryAudit()
    ref = SnapshotCapture(store=LocalSnapshotStore(root=tmp_path),
                          audit=audit).capture(FINDING.id, PAGE)
    w = DossierWriter(deps=HarnessDeps(audit=audit),
                      generate=FakeModel(good_draft(
                          build_bundle(FINDING, MIRA, verdict(), snapshot=ref,
                                       consents=[GRANT]))),
                      sleep=lambda _s: None)

    dossier = w.for_finding(FINDING, MIRA, verdict(), snapshot=ref,
                            consents=[GRANT])

    assert dossier.ok is True
    assert dossier.bundle.clause.text == CLAUSE
    assert dossier.bundle.snapshot_uris
    assert dossier.sendable is False
    # .get, because the harness writes its own envelope rows alongside ours
    assert {e.get("event") for e in audit.events} >= {"snapshot", "dossier"}
