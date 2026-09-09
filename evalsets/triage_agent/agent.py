"""The agent `adk eval` points at for the `adversarial_injection` evalset.

    adk eval evalsets/triage_agent \
             evalsets/adversarial_injection.evalset.json \
             --config_file_path evalsets/test_config.json

ADK discovers `root_agent` in a package's `agent` module, so this file exists
only to expose the WU-09 Triage agent under that name. The agent itself — the
schema, the instruction, temperature 0, `tools=()` — is defined in
`consentinel/agents/triage.py` and not duplicated here; an eval that ran against
a copy of the agent would prove nothing about the one we ship.

The instruction is built with a **fixed** fence token rather than the
per-call random one. In production the token is a fresh `uuid4` slice so a page
cannot forge the end of its own block; here it has to be stable, or every eval
run would compare against a different prompt. The evalset feeds page text
directly as the user turn, which is the same trust boundary with one fewer
moving part.

Running this needs Vertex credentials. `tests/test_evalsets.py` asserts the same
pairs deterministically, so CI enforces the security property on every commit
without a model.
"""

from __future__ import annotations

from consentinel.agents.triage import build_agent, build_instruction

EVAL_FENCE = "UNTRUSTED-PAGE-EVAL"

root_agent = build_agent(instruction=build_instruction(EVAL_FENCE))
