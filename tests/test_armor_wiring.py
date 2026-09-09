"""WU-29 — the screen is connected, and asks for templates that exist.

`consentinel/model_armor.py` had 24 tests and worked. It also never ran: the
app built `HarnessDeps` without an `armor=`, so the harness used `NullArmor`,
which returns a clean verdict forever. And the one agent that did name a
template asked for `consentinel-ingest`, which does not exist on the project —
the templates are `consentinel-{triage,ingest}-in` and
`consentinel-notice-out`.

Either fault is invisible from the outside. A screen that never runs and a
screen that finds nothing produce the same logs, the same verdicts and the same
audit rows. Both would have been discovered by a judge asking "so what does
Model Armor catch?" during the demo.

So these tests assert the wiring rather than the behaviour: something real is
installed, and every template any agent asks for is one that exists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from consentinel.harness.ports import HarnessDeps, NullArmor
from consentinel.model_armor import (
    INBOUND_TEMPLATES,
    INGEST_IN,
    NOTICE_OUT,
    TRIAGE_IN,
    ModelArmor,
    is_configured,
)

KNOWN = {TRIAGE_IN, INGEST_IN, NOTICE_OUT}

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "infra" / "model_armor" / "01_templates.sh"


# ------------------------------------------------------- the acceptance tests

def test_the_app_installs_a_real_screen_not_the_no_op():
    """The fault this file exists for. `HarnessDeps.armor` defaults to
    `NullArmor`, so forgetting one keyword argument disables the whole feature
    silently."""
    from web.app import get_armor

    armor = get_armor()

    assert isinstance(armor, ModelArmor)
    assert not isinstance(armor, NullArmor)
    assert is_configured(armor)


def test_the_default_is_a_no_op_which_is_why_the_test_above_is_needed():
    """Pins the trap rather than the fix, so nobody 'simplifies' the wiring
    away on the grounds that the default looks harmless."""
    assert isinstance(HarnessDeps().armor, NullArmor)
    assert not is_configured(HarnessDeps().armor)


def test_every_template_an_agent_asks_for_actually_exists():
    """The general form of the `consentinel-ingest` bug.

    Found by reading the source rather than the policies, because not every
    agent exposes its `HarnessPolicy` as a module constant — triage builds one
    in a property. A new agent that invents a template name fails here, rather
    than logging `armor_unavailable` once and screening nothing forever.
    """
    for where, arg, template in _declared_templates():
        assert template in KNOWN, (
            f"{where} asks for armor template {template!r} via {arg}, which is "
            f"not one of {sorted(KNOWN)}. Create it in "
            f"infra/model_armor/01_templates.sh and name it in model_armor.py.")


def test_the_infra_script_creates_exactly_the_templates_the_code_names():
    """Closes the loop the other way. The constants can be right and the script
    can still create something else — which is exactly how these drifted
    apart."""
    created = set(re.findall(r"^create (consentinel-[\w-]+)",
                             SCRIPT.read_text(encoding="utf-8"),
                             flags=re.MULTILINE))

    assert created == KNOWN


# ------------------------------------------------------------------ direction

def test_the_inbound_templates_are_the_two_that_read_untrusted_text():
    """Direction decides what a failure means: inbound labels, outbound blocks.
    Getting this list wrong makes a failed screen fail the wrong way."""
    assert set(INBOUND_TEMPLATES) == {TRIAGE_IN, INGEST_IN}
    assert NOTICE_OUT not in INBOUND_TEMPLATES


def test_a_contract_is_screened_inbound_so_a_bad_template_cannot_block_an_upload():
    """The upload path has to survive a missing template. Only the outbound
    notice screen blocks on failure, and it is the one thing a human sends."""
    from consentinel.agents.consent_ingest.agent import POLICY

    assert POLICY.armor_prompt in INBOUND_TEMPLATES
    assert getattr(POLICY, "armor_response", None) is None


def test_page_text_is_screened_inbound():
    """Triage keeps its own alias for the name. It has to agree with the one
    `model_armor` owns — the resolver test above would catch a drifted value,
    but a reader looking for this pairing should find it stated."""
    from consentinel.agents.triage import ARMOR_TEMPLATE_IN

    assert ARMOR_TEMPLATE_IN == TRIAGE_IN
    assert ARMOR_TEMPLATE_IN in INBOUND_TEMPLATES


def test_the_drafted_notice_is_screened_outbound():
    """Hard rule 6's other half: the one artefact aimed at a human gets the
    template that blocks."""
    from consentinel.agents.dossier_writer import ARMOR_TEMPLATE_OUT

    assert ARMOR_TEMPLATE_OUT == NOTICE_OUT
    assert ARMOR_TEMPLATE_OUT not in INBOUND_TEMPLATES


# -------------------------------------------------------------------- helpers

ARMOR_ARGS = ("armor_prompt", "armor_response")


def _armor_keywords() -> list[tuple[Path, ast.keyword]]:
    """Every `armor_prompt=`/`armor_response=` in the agents package.

    Source-level, so it catches a policy built anywhere — a module constant, a
    property, a factory — not just the ones exposed as importable objects.
    """
    found: list[tuple[Path, ast.keyword]] = []
    for path in sorted((REPO / "consentinel" / "agents").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg in ARMOR_ARGS:
                found.append((path, node))
    return found


def _module_of(path: Path) -> object:
    from importlib import import_module

    parts = path.relative_to(REPO).with_suffix("").parts
    return import_module(".".join(parts))


def _declared_templates() -> list[tuple[str, str, str]]:
    """`(where, arg, resolved template name)` for each declaration.

    A name is resolved by importing its module and reading the attribute, so
    the assertion is about the value that reaches Model Armor rather than the
    spelling of the identifier.
    """
    out: list[tuple[str, str, str]] = []
    for path, node in _armor_keywords():
        where = str(path.relative_to(REPO))
        if isinstance(node.value, ast.Constant):
            out.append((where, node.arg, node.value.value))
        elif isinstance(node.value, ast.Name):
            out.append((where, node.arg,
                        getattr(_module_of(path), node.value.id)))
    return out


def test_the_helper_found_something_to_check():
    """A collector that silently finds nothing turns the tests above into
    no-ops that pass forever."""
    found = _declared_templates()

    assert len(found) >= 3, found          # ingest in, triage in, notice out
    assert {arg for _, arg, _ in found} == set(ARMOR_ARGS)


def test_no_agent_retypes_a_template_name_as_a_literal():
    """`model_armor` owns these names. A literal is how the ingest template
    drifted from `-in` in the first place, and the cost was the entire feature
    on that path — a screen that reported `armor_unavailable` forever."""
    offenders = [
        f"{path.relative_to(REPO)}: {node.arg}={node.value.value!r} "
        f"— import the constant from consentinel.model_armor instead"
        for path, node in _armor_keywords()
        if isinstance(node.value, ast.Constant) and node.value.value is not None
    ]

    assert not offenders, "\n".join(offenders)
