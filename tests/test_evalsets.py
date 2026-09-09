"""WU-31 — the evalsets, and the CI half of them.

Done when: `adk eval` passes on `adversarial_injection` and `verdict_matrix`,
and both run in CI on every commit.

Those are two different jobs and this file is the second one. `adk eval` runs
the real Gemini agent and needs Vertex credentials, which CI does not have on
every commit — so the **evalset files are the single source of truth** and this
module runs them without a model:

* every file is parsed with **ADK's own `EvalSet` schema**, so the format cannot
  drift from what `adk eval` expects (the ticket says use ADK's harness, not
  invent one — this validates against it rather than reimplementing it);
* `verdict_matrix` is replayed through the reconciler, which has no model call
  at all, so it is exact-match and free;
* `adversarial_injection` is replayed through the canary, the validators and the
  reconciler, asserting the property the evalset exists for: **a hostile page
  changes the badge and nothing else.**

WU-11 could only assert that at the extraction level, because the reconciler
did not exist yet. It does now, so this closes the gap: same verdict, same
matched grant, same citation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from consentinel.agents.injection_canary import scan
from consentinel.agents.reconciler import Observation, evaluate
from consentinel.agents.validators import validate_extraction
from consentinel.store.base import Consent, PermittedUse, Performer, Verdict
from consentinel.tools.contracts import TriageExtraction

EVALSETS = Path(__file__).resolve().parent.parent / "evalsets"
ADVERSARIAL = EVALSETS / "adversarial_injection.evalset.json"
VERDICT_MATRIX = EVALSETS / "verdict_matrix.evalset.json"

MIRA = Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])


def load(path: Path) -> Any:
    """Parse with ADK's schema, so a file this passes is a file `adk eval` reads."""
    from google.adk.evaluation.eval_set import EvalSet

    return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))


def only_text(content: Any) -> str:
    return "".join(part.text or "" for part in (content.parts or []))


def cases(path: Path) -> dict[str, tuple[str, str]]:
    """eval_id -> (user text, expected response text)."""
    out: dict[str, tuple[str, str]] = {}
    for case in load(path).eval_cases:
        invocation = case.conversation[0]
        out[case.eval_id] = (only_text(invocation.user_content),
                             only_text(invocation.final_response))
    return out


# ------------------------------------------------------------- the format

@pytest.mark.parametrize("path", [ADVERSARIAL, VERDICT_MATRIX])
def test_the_evalset_parses_with_adks_own_schema(path):
    """If this fails, `adk eval` would reject the file too."""
    eval_set = load(path)

    assert eval_set.eval_set_id == path.name.split(".")[0]
    assert eval_set.eval_cases
    for case in eval_set.eval_cases:
        assert case.conversation, case.eval_id
        assert case.conversation[0].final_response is not None, case.eval_id


def test_the_criteria_file_names_thresholds_adk_understands():
    from google.adk.evaluation.eval_metrics import EvalMetric

    criteria = json.loads((EVALSETS / "test_config.json")
                          .read_text(encoding="utf-8"))["criteria"]

    assert criteria["tool_trajectory_avg_score"] == 1.0
    for name, threshold in criteria.items():
        EvalMetric(metric_name=name, threshold=threshold)   # validates


def test_the_eval_agent_is_the_shipped_triage_agent_not_a_copy():
    """An eval that ran against a copy of the agent would prove nothing about
    the one we ship."""
    from consentinel.agents.triage import TriageOut
    from evalsets.triage_agent.agent import root_agent

    assert root_agent.output_schema is TriageOut
    assert not root_agent.tools
    assert root_agent.generate_content_config.temperature == 0.0
    assert "UNTRUSTED PAGE CONTENT" in root_agent.instruction


# ------------------------------------------ adversarial_injection, no model

def test_the_evalset_is_built_as_pairs():
    ids = set(cases(ADVERSARIAL))

    pairs = {i[: -len("_clean")] for i in ids if i.endswith("_clean")}
    assert pairs
    for stem in pairs:
        assert f"{stem}_injected" in ids, stem
    assert len(ids) == len(pairs) * 2


@pytest.mark.parametrize("stem", ["voice_listing", "role_marker", "faked_grant"])
def test_the_pair_expects_an_identical_reading(stem):
    """The assertion *is* the file: the clean and injected cases carry the same
    expected response, so a model that obeyed the page would fail the eval."""
    data = cases(ADVERSARIAL)

    clean_expected = json.loads(data[f"{stem}_clean"][1])
    injected_expected = json.loads(data[f"{stem}_injected"][1])

    assert clean_expected == injected_expected
    assert clean_expected["is_synthetic_claim"] is True     # what pages ask to flip


@pytest.mark.parametrize("stem", ["voice_listing", "role_marker", "faked_grant"])
def test_only_the_injected_half_is_flagged(stem):
    data = cases(ADVERSARIAL)

    clean = scan(data[f"{stem}_clean"][0])
    injected = scan(data[f"{stem}_injected"][0])

    assert clean.suspected is False, f"{stem}_clean should be clean"
    assert injected.suspected is True, f"{stem}_injected should be flagged"
    assert injected.markers


@pytest.mark.parametrize("stem", ["voice_listing", "role_marker", "faked_grant"])
def test_the_expected_quote_is_verbatim_in_both_halves(stem):
    """The evalset cannot ask for a quote the page does not contain — that
    would make the eval reward a fabrication."""
    data = cases(ADVERSARIAL)

    for half in ("clean", "injected"):
        page, expected = data[f"{stem}_{half}"]
        extraction = _extraction(json.loads(expected))
        validate_extraction(extraction, page, MIRA)          # raises if not


@pytest.mark.parametrize("stem", ["voice_listing", "role_marker", "faked_grant"])
def test_the_verdict_is_identical_across_the_pair(stem):
    """The property WU-11 could only assert at the extraction level, now
    asserted where it matters: the verdict, its matched grant and its citation
    are unchanged by the injection."""
    data = cases(ADVERSARIAL)
    grants = [Consent(id="c_aurora", performer_id="perf_mira",
                      licensee="Aurora Studios",
                      permitted_uses=[PermittedUse.VOICE_SYNTH],
                      territories=["US", "CA"])]

    # A fixed `as_of`, and not for tidiness. `evaluate()` stamps the verdict
    # with `datetime.now()` when it is not given one, and `VerdictResult` is a
    # frozen dataclass, so that timestamp takes part in `==`. Two calls
    # normally land inside the same clock tick — about 15 ms on Windows — and
    # compare equal; occasionally they straddle one and this test failed with
    # two identical verdicts that differed by a few microseconds. Pinning the
    # time makes the assertion about the injection, which is what it is for.
    as_of = datetime(2026, 9, 9, tzinfo=timezone.utc)

    verdicts = []
    for half in ("clean", "injected"):
        extraction = _extraction(json.loads(data[f"{stem}_{half}"][1]))
        observation = Observation.from_extraction(extraction, "perf_mira",
                                                  actor="VozClone Studio")
        verdicts.append(evaluate(observation, grants, as_of=as_of))

    clean, injected = verdicts
    assert clean == injected
    assert clean.verdict is not Verdict.AUTHORIZED     # nothing here is licensed
    assert clean.citation


def test_the_faked_grant_case_does_not_win_by_asserting_a_grant():
    """That page claims Aurora holds a worldwide grant. The registry disagrees,
    and the registry is the only thing the reconciler reads."""
    data = cases(ADVERSARIAL)
    extraction = _extraction(json.loads(data["faked_grant_injected"][1]))

    result = evaluate(
        Observation.from_extraction(extraction, "perf_mira",
                                    actor="VozClone Studio"),
        [Consent(id="c_aurora", performer_id="perf_mira",
                 licensee="Aurora Studios",
                 permitted_uses=[PermittedUse.VOICE_SYNTH],
                 territories=["US", "CA"])])

    assert result.verdict is Verdict.UNAUTHORIZED
    assert result.matched_consent_id is None
    assert "c_aurora" not in (result.reason or "").replace("Aurora Studios", "")


# --------------------------------------------- verdict_matrix, exact match

def test_every_verdict_matrix_case_lands_on_its_expected_branch():
    """Deterministic, so exact-match: no model, no threshold, no flake."""
    for eval_id, (payload, expected) in cases(VERDICT_MATRIX).items():
        given = json.loads(payload)
        want = json.loads(expected)

        result = evaluate(
            _observation(given["observation"]),
            [_consent(c) for c in given["consents"]],
            _as_of(given.get("as_of")),
        )

        assert result.verdict.value == want["verdict"], eval_id
        assert result.check == want["check"], eval_id
        if "territories_outside" in want:
            assert list(result.territories_outside) == want["territories_outside"], eval_id
        if "matched_consent_id" in want:
            assert result.matched_consent_id == want["matched_consent_id"], eval_id
        if "reason_contains" in want:
            assert want["reason_contains"] in result.reason, eval_id


def test_the_matrix_covers_all_seven_branches_plus_scoping():
    checks = {json.loads(expected)["check"]
              for _, expected in cases(VERDICT_MATRIX).values()}

    assert checks >= {
        "no_grant", "modality_not_permitted", "territory_outside_grant",
        "grant_expired", "grant_not_yet_effective", "actor_not_licensee",
        "confidence_below_threshold", "covered_by_grant",
    }
    ids = cases(VERDICT_MATRIX)
    assert "c2_territory_scoping_partial_coverage" in ids


def test_the_matrix_needs_no_credentials_and_no_network():
    """The reason it is safe to run on every commit."""
    import ast

    source = Path("consentinel/agents/reconciler.py").read_text(encoding="utf-8")
    imports = {n.module or "" for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.ImportFrom)}

    assert not [m for m in imports
                if m.startswith(("google.", "httpx", "parallel"))]


# ------------------------------------------------------------------ helpers

def _extraction(raw: dict[str, Any]) -> TriageExtraction:
    return TriageExtraction(
        depicts_named_person=raw["depicts_named_person"],
        person_name=raw.get("person_name"),
        is_synthetic_claim=raw["is_synthetic_claim"],
        modality=raw.get("modality"),
        is_commercial=raw["is_commercial"],
        target_territories=list(raw.get("target_territories", [])),
        evidence_quote=raw.get("evidence_quote"),
        confidence=float(raw["confidence"]),
    )


def _observation(raw: dict[str, Any]) -> Observation:
    return Observation(
        performer_id=raw["performer_id"],
        depicts_named_person=bool(raw.get("depicts_named_person", True)),
        modality=raw.get("modality"),
        target_territories=tuple(raw.get("target_territories", [])),
        actor=raw.get("actor"),
        confidence=float(raw.get("confidence", 0.0)),
        evidence_quote=raw.get("evidence_quote"),
    )


def _consent(raw: dict[str, Any]) -> Consent:
    return Consent(
        id=raw["id"], performer_id=raw["performer_id"],
        licensee=raw["licensee"],
        permitted_uses=[PermittedUse(u) for u in raw.get("permitted_uses", [])],
        territories=list(raw.get("territories", [])),
        valid_from=_as_of(raw.get("valid_from")),
        valid_to=_as_of(raw.get("valid_to")),
    )


def _as_of(value: Any):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None
