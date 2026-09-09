"""WU-24 — deploy the agents to Vertex AI Agent Engine.

Why this exists at all: the rules say *"powered by Gemini and Google Cloud
Agent Builder"*, and Cloud Run alone makes that a claim about prose rather than
about code. After this runs, the agents exist as Agent Engine resources you can
list, call and point a judge at.

**Four runtimes, matching `DESIGN.md` §2.** One per pipeline, plus Triage on its
own because it is the only component that reads hostile third-party content and
deployment separation is what contains a stolen credential.

    cn-ingest        ConsentIngest         consentinel-ingest@
    cn-triage        Triage                consentinel-triage@      hostile input
    cn-clearance     ClearanceInspector    consentinel-clearance@
    cn-enforcement   QueryPlanner          consentinel-enforcement@

**What is deployed and what is not — say this accurately.** Each runtime hosts
its pipeline's *model* step, built by that agent's own `build_agent()`, with the
same prompt the app uses. The deterministic parts stay on Cloud Run next to the
registry: the reconciler decides every verdict and it has no model, the harness
runs the validators, and the rule engine cannot be moved to a runtime without
also moving the registry it reads. So: the LLM agents run on Agent Engine, the
decisions run in code. That was already the design; this just puts the model
steps where the rules ask for them.

Every deployed agent sets `output_schema`, and with it set ADK refuses tools and
agent transfer. Which means "no tools, no free-form output channel" stops being
a promise in a prompt and becomes a property of the runtime.

Usage:

    python -m infra.agent_engine.deploy --list
    python -m infra.agent_engine.deploy --runtime cn-ingest
    python -m infra.agent_engine.deploy --all
    python -m infra.agent_engine.deploy --runtime cn-triage --delete

The staging bucket is created on first run if it is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "consentinel")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
STAGING = f"gs://{PROJECT}-agent-staging"

# Where the deployed resource names land. Committed, because "we deployed to
# Agent Engine" is a submission claim and a claim needs something to point at.
RECORD = REPO / "infra" / "agent_engine" / "deployed.json"


@dataclass(frozen=True)
class Runtime:
    name: str
    description: str
    service_account: str
    build: Callable[[], Any]


def _runtimes() -> dict[str, Runtime]:
    """Built lazily: importing ADK is slow and `--list` should not pay for it."""
    from consentinel.agents import query_planner, triage
    from consentinel.agents.clearance.agent import build_agent as build_clearance
    from consentinel.agents.consent_ingest.agent import build_agent as build_ingest

    def sa(name: str) -> str:
        return f"consentinel-{name}@{PROJECT}.iam.gserviceaccount.com"

    return {
        "cn-ingest": Runtime(
            "cn-ingest",
            "Reads a signed performer agreement and fills in a permission slip, "
            "quoting the sentence behind every field. No tools.",
            sa("ingest"),
            build_ingest,
        ),
        "cn-triage": Runtime(
            "cn-triage",
            "Reads one untrusted web page and answers a fixed set of questions "
            "about it. Isolated on its own runtime: hostile input, no tools, no "
            "secrets, no write access.",
            sa("triage"),
            triage.build_agent,
        ),
        "cn-clearance": Runtime(
            "cn-clearance",
            "Looks at one of our own clips and reports what is perceptible in "
            "it. Never asked who the person is; its answer can withhold "
            "clearance but never grant it.",
            sa("clearance"),
            build_clearance,
        ),
        "cn-enforcement": Runtime(
            "cn-enforcement",
            "Writes the search phrases for a sweep, in five languages, from a "
            "performer's registry entry.",
            sa("enforcement"),
            query_planner.build_agent,
        ),
    }


# Not installed in an agent container. The dev tools are obvious; the web
# packages are there because the runtime serves no HTTP of its own — Cloud Run
# does that. Every one of them is another chance for the build to fail on a
# dependency it never needed.
SKIP = {"pytest", "ruff", "fastapi", "uvicorn[standard]", "jinja2", "python-multipart"}


def requirements() -> list[str]:
    """The app's own requirements, minus the dev tools.

    The deployed package is our real `consentinel` package rather than a copy of
    the prompt, so the container needs what our imports need. Sending the same
    file the app uses is the only version of this that cannot drift.
    """
    import importlib.metadata as meta

    out = []
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line in SKIP:
            continue
        out.append(line)

    # Pin ADK to this machine's version, and keep that version current.
    #
    # The agent object is *pickled* here and unpickled in the container, so the
    # two ADKs have to agree. Both directions of disagreement fail, and neither
    # says so:
    #
    #   container newer than us  ->  every call answers
    #                                "'LlmAgent' object has no attribute 'mode'"
    #   container older than us  ->  it will not start:
    #                                "Runner.__init__() got an unexpected
    #                                 keyword argument 'auto_create_session'"
    #
    # The second one is the tell that matters: `auto_create_session` is passed
    # by *Agent Engine's own serving code*, so the platform tracks a recent ADK
    # and pinning ours backwards breaks it. Keep this machine on a current
    # `google-adk` rather than pinning an old one here.
    #
    # `google-cloud-aiplatform` is deliberately not pinned — ADK constrains it,
    # and pinning both invites a resolver conflict in the container.
    try:
        out.append(f"google-adk=={meta.version('google-adk')}")
    except meta.PackageNotFoundError:
        pass
    return out


def ensure_staging_bucket() -> None:
    """Create the staging bucket if it is missing.

    Through the storage client rather than `gcloud`: on Windows the executable
    is `gcloud.cmd` and a bare `subprocess.run(["gcloud", ...])` dies with
    WinError 2 before anything useful happens.
    """
    from google.cloud import storage

    client = storage.Client(project=PROJECT)
    name = STAGING.removeprefix("gs://")
    if client.lookup_bucket(name) is not None:
        return
    print(f"creating staging bucket {STAGING}")
    client.create_bucket(name, location=LOCATION)


def deploy(runtime: Runtime, *, with_service_account: bool = True) -> str:
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING)

    # `extra_packages` paths are resolved and tarred **relative to the working
    # directory**, so this has to run from the repo root for the archive to
    # contain `consentinel/` at its top level. An absolute path builds a
    # tarball the container unpacks somewhere `import consentinel` cannot see
    # it, and the only symptom is the deployment failing to start with
    # "No module named 'consentinel'" — after the build has already succeeded.
    os.chdir(REPO)

    app = agent_engines.AdkApp(agent=runtime.build(), enable_tracing=True)

    kwargs: dict[str, Any] = dict(
        agent_engine=app,
        display_name=runtime.name,
        description=runtime.description,
        requirements=requirements(),
        extra_packages=["consentinel"],
        # A staging directory per runtime. Without this every deployment
        # pickles its agent to the *same* object —
        # `gs://.../agent_engine/agent_engine.pkl` — so two deploys running at
        # once overwrite each other and a runtime comes up serving whichever
        # agent won the race. It is invisible from the outside: the deployment
        # is healthy, answers promptly, and answers as the wrong agent. Caught
        # only because `cn-triage` replied with a search plan.
        gcs_dir_name=f"agent_engine/{runtime.name}",
        # No `env_vars` for project or location: Agent Engine sets both itself
        # and rejects the deployment outright if you pass them
        # ("Environment variable name 'GOOGLE_CLOUD_PROJECT' is reserved").
        # Our code reads them from the environment either way, so there is
        # nothing to replace them with.
    )
    if with_service_account:
        # Per-runtime identity is the point of splitting them at all: Triage
        # holding its own least-privileged account is what makes deployment
        # separation worth the extra deploys.
        kwargs["service_account"] = runtime.service_account

    print(f"deploying {runtime.name} ... this takes several minutes")
    engine = agent_engines.create(**kwargs)
    print(f"  {runtime.name} -> {engine.resource_name}")
    return engine.resource_name


def record(name: str, resource: str) -> None:
    data = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.exists() else {}
    data[name] = {
        "resource_name": resource,
        "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": PROJECT,
        "location": LOCATION,
    }
    RECORD.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  recorded in {RECORD.relative_to(REPO)}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="infra.agent_engine.deploy",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--runtime", help="one of cn-ingest, cn-triage, cn-clearance, cn-enforcement")
    ap.add_argument("--all", action="store_true", help="deploy all four")
    ap.add_argument("--list", action="store_true", help="list what is deployed now")
    ap.add_argument("--delete", action="store_true", help="delete the named runtime")
    ap.add_argument("--no-service-account", action="store_true",
                    help="deploy with the default Agent Engine identity "
                         "(fallback if a per-runtime account is not usable yet)")
    args = ap.parse_args(argv)

    if args.list:
        import vertexai
        from vertexai import agent_engines
        vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING)
        found = list(agent_engines.list())
        if not found:
            print("nothing deployed")
        for e in found:
            print(f"{e.display_name:<16} {e.resource_name}")
        return 0

    if args.delete:
        if not args.runtime:
            print("--delete needs --runtime", file=sys.stderr)
            return 2
        import vertexai
        from vertexai import agent_engines
        vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING)
        for e in agent_engines.list():
            if e.display_name == args.runtime:
                e.delete(force=True)
                print(f"deleted {args.runtime}")
        return 0

    targets = list(_runtimes().values()) if args.all else None
    if targets is None:
        if not args.runtime:
            print("pass --runtime NAME, --all, or --list", file=sys.stderr)
            return 2
        table = _runtimes()
        if args.runtime not in table:
            print(f"unknown runtime {args.runtime!r}; have {', '.join(table)}", file=sys.stderr)
            return 2
        targets = [table[args.runtime]]

    ensure_staging_bucket()

    failures = 0
    for runtime in targets:
        try:
            resource = deploy(runtime, with_service_account=not args.no_service_account)
            record(runtime.name, resource)
        except Exception as exc:                      # noqa: BLE001 - report and continue
            failures += 1
            print(f"  {runtime.name} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
