"""WU-30 — traces, logs and metrics.

All offline. The thing most worth testing here is the rule about what never
reaches a log, because that one fails silently and in production.
"""

from __future__ import annotations

import io
import json

import pytest

from consentinel.obs import (
    NEVER_LOG,
    JsonLogger,
    MemoryTracer,
    Metrics,
    describe,
    safe_fields,
)


def lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(l) for l in buf.getvalue().strip().split("\n") if l.strip()]


@pytest.fixture
def buf():
    return io.StringIO()


# ------------------------------------------------------ what never gets logged


def test_page_content_never_reaches_a_log(buf):
    """It is attacker-controlled and may carry personal data, and logs are
    retained and widely readable. So we log about content, never content."""
    log = JsonLogger(stream=buf)
    log.info("triaged", url="https://x.invalid/a",
             page_text="the secret contents of a hostile page",
             evidence_quote="AI voice of Mira Vance")

    raw = buf.getvalue()
    assert "secret contents" not in raw
    assert "Mira Vance" not in raw

    entry = lines(buf)[0]
    assert entry["url"] == "https://x.invalid/a"          # metadata is fine
    assert entry["page_text"].startswith("<")             # replaced by a description
    assert "bytes" in entry["page_text"] and "sha256" in entry["page_text"]


def test_the_description_is_enough_to_debug_with():
    """A hash and a length let you tell two pages apart and spot an empty one,
    without ever storing what they said."""
    a, b = describe("hello world"), describe("hello worlds")
    assert a != b
    assert "11 bytes" in a
    assert describe(None) == "none"
    assert describe([1, 2, 3]) == "<3 items>"


def test_forbidden_fields_are_stripped_even_when_nested():
    out = safe_fields({"ok": True, "inner": {"quote": "verbatim text", "page": 4}})
    assert out["ok"] is True
    assert out["inner"]["page"] == 4
    assert "verbatim text" not in json.dumps(out)


def test_the_forbidden_list_covers_the_obvious_ones():
    for name in ("text", "page_text", "quote", "evidence_quote", "prompt", "transcript",
                 "draft_notice", "reasoning"):
        assert name in NEVER_LOG, f"{name} should never be loggable"


def test_a_log_line_can_be_joined_to_its_trace(buf):
    """This is what makes a log line clickable from a trace in the console, which
    is what turns two tools into one story."""
    log = JsonLogger(stream=buf, project="consentinel")
    log.info("hello", trace_id="a" * 32, span_id="b" * 16)
    entry = lines(buf)[0]
    assert entry["logging.googleapis.com/trace"] == "projects/consentinel/traces/" + "a" * 32
    assert entry["logging.googleapis.com/spanId"] == "b" * 16


def test_severity_is_a_field_cloud_logging_understands(buf):
    log = JsonLogger(stream=buf)
    log.warning("careful")
    log.error("broken")
    assert [e["severity"] for e in lines(buf)] == ["WARNING", "ERROR"]


# -------------------------------------------------------------------- metrics


def test_the_two_security_metrics_are_recorded(buf):
    """These are the guardrails' own telemetry. A non-zero validation-failure
    rate is the model trying to fabricate a citation and being caught."""
    m = Metrics(JsonLogger(stream=buf))
    m.counter("extraction.validation_failures", reason="quote_not_verbatim")
    m.counter("extraction.validation_failures", reason="quote_not_verbatim")
    m.counter("extraction.validation_failures", reason="name_mismatch")
    m.counter("model_armor.detections", type="injection", path="triage")

    assert m.total("extraction.validation_failures") == 3
    assert m.total("extraction.validation_failures", reason="name_mismatch") == 1
    assert m.total("model_armor.detections", type="injection", path="triage") == 1


def test_every_metric_is_also_a_log_line(buf):
    """Log-based metrics are the aggregation strategy, so the line has to carry
    the name, the kind and the value."""
    m = Metrics(JsonLogger(stream=buf))
    m.histogram("agent.duration_seconds", 12.75, agent="consent_ingest")
    entry = lines(buf)[0]
    assert entry["metric"] == "agent.duration_seconds"
    assert entry["kind"] == "distribution"
    assert entry["value"] == 12.75
    assert entry["agent"] == "consent_ingest"


def test_a_mistyped_metric_name_warns_rather_than_vanishing(buf):
    """Otherwise it silently never appears on the dashboard and nobody notices
    until they go looking for it."""
    m = Metrics(JsonLogger(stream=buf))
    m.counter("verdicts.totl", verdict="unauthorized")     # typo
    warn = [e for e in lines(buf) if e["severity"] == "WARNING"]
    assert warn and "unknown metric" in warn[0]["message"]


def test_a_distribution_summarises(buf):
    m = Metrics(JsonLogger(stream=buf))
    for v in (1.0, 3.0, 5.0):
        m.histogram("tool.latency_seconds", v, tool="parallel_search")
    snap = m.snapshot()["tool.latency_seconds{tool=parallel_search}"]
    assert snap == {"count": 3, "mean": 3.0, "max": 5.0}


# --------------------------------------------------------------------- traces


def test_a_sweep_reads_as_one_tree():
    t = MemoryTracer()
    with t.span("sweep", {"performer": "p1"}):
        with t.span("plan", {}):
            pass
        with t.span("triage[find_0003]", {}):
            with t.span("fetch_page", {}):
                pass
    assert t.tree() == ["sweep", "  plan", "  triage[find_0003]", "    fetch_page"]


def test_attributes_set_during_the_call_are_kept():
    """The harness fills in the verdict and cache age *during* the span, so they
    have to be readable after it closes."""
    t = MemoryTracer()
    with t.span("reconcile", {"agent": "reconciler"}) as attrs:
        attrs["verdict"] = "unauthorized"
        attrs["failing_check"] = "territory"
    assert t.roots[0].attributes["verdict"] == "unauthorized"
    assert t.roots[0].attributes["failing_check"] == "territory"


def test_a_failing_span_records_why_and_still_reraises():
    t = MemoryTracer()
    with pytest.raises(ValueError):
        with t.span("triage", {}):
            raise ValueError("bad page")
    assert "bad page" in t.roots[0].error


def test_the_harness_traces_without_being_asked():
    from consentinel.harness.policy import FailState, HarnessPolicy
    from consentinel.harness.ports import HarnessDeps
    from consentinel.harness.runner import Harness

    t = MemoryTracer()
    m = Metrics(JsonLogger(stream=io.StringIO()))
    policy = HarnessPolicy(agent_name="demo", fail_state=FailState.AMBIGUOUS, output_schema=dict)
    Harness(policy, HarnessDeps(tracer=t, metrics=m, prompt_version="v1")).run(
        lambda hint: {"a": 1}, subject_id="f1"
    )

    assert t.tree() == ["agent.demo"]
    assert t.roots[0].attributes["prompt_version"] == "v1"
    assert m.total("agent.runs", agent="demo", outcome="ok") == 1
    assert m.values("agent.duration_seconds", agent="demo")


def test_tracer_choice_needs_no_flag_in_deploy(monkeypatch):
    """Cloud Trace when there is a project, memory otherwise - so a laptop and a
    test need no credentials and deploy needs no configuration."""
    from consentinel.obs import CloudTracer, tracer_for

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("CONSENTINEL_TRACE", raising=False)
    assert isinstance(tracer_for(), MemoryTracer)

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "consentinel")
    assert isinstance(tracer_for(), CloudTracer)

    monkeypatch.setenv("CONSENTINEL_TRACE", "memory")
    assert isinstance(tracer_for(), MemoryTracer), "an escape hatch for local runs"
