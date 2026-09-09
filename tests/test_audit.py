"""WU-18 — the audit trail.

Runs against an in-memory store, so no network. What is tested here is the
three properties the trail is meant to have: it cannot be edited, it says how
old its inputs were, and it records failures as loudly as successes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from consentinel.audit import FirestoreAudit
from consentinel.harness.policy import FailState, HarnessPolicy
from consentinel.harness.ports import HarnessDeps
from consentinel.harness.runner import Harness
from consentinel.store.base import AuditEvent, Store


class MemStore(Store):
    def __init__(self):
        self.audit: list[AuditEvent] = []

    def append_audit(self, e: AuditEvent): self.audit.append(e)
    def list_audit(self, subject_type=None, subject_id=None):
        return [e for e in self.audit
                if (subject_id is None or e.subject_id == subject_id)
                and (subject_type is None or e.subject_type == subject_type)]

    # the rest of the interface is unused here
    def upsert_performer(self, p): ...
    def get_performer(self, i): ...
    def list_performers(self): return []
    def upsert_consent(self, c): ...
    def list_consents(self, i): return []
    def upsert_finding(self, f): ...
    def get_finding(self, i): ...
    def list_findings(self, **kw): return []
    def upsert_asset(self, a): ...
    def list_assets(self, i, state=None): return []
    def put_dossier(self, d): ...
    def get_dossier(self, i): ...


@pytest.fixture
def audit():
    return FirestoreAudit(MemStore(), actor="test_agent", subject_type="finding")


# --------------------------------------------------------------- append-only


def test_there_is_no_way_to_edit_or_delete_a_row(audit):
    """Removing the capability beats remembering not to use it. If someone adds
    a delete later, this fails and asks them why."""
    for forbidden in ("update_audit", "delete_audit", "clear_audit", "edit_audit"):
        assert not hasattr(audit, forbidden)
        assert not hasattr(audit.store, forbidden)


def test_every_row_gets_a_time_ordered_id(audit):
    for i in range(3):
        audit.append({"actor": "a", "subject_id": f"s{i}"})
    ids = [e.id for e in audit.store.audit]
    assert ids == sorted(ids), "ids must sort chronologically without an index"
    assert len(set(ids)) == 3, "two writes in the same millisecond must not collide"


# ------------------------------------------------------- how old the data was


def test_a_row_says_whether_its_input_came_from_cache(audit):
    """A decision must never imply a freshness it does not have. Claiming we
    checked the web at 3pm when the answer came from a 9am cache is the quiet
    dishonesty that makes a trail worthless."""
    audit.append({
        "actor": "triage", "subject_id": "f1",
        "tool_calls": [{"from_cache": True, "cache_age_s": 32400.0}],
    })
    call = audit.store.audit[0].tool_calls[0]
    assert call["from_cache"] is True
    assert call["cache_age_s"] == 32400.0


def test_a_tool_call_is_timed_and_folded_into_the_agent_row(audit):
    with audit.tool("parallel_search", query="mira vance ai voice", locale="pt-BR") as call:
        call.record(from_cache=False, result_count=7)

    audit.append({"actor": "text_sweep", "subject_id": "sweep1"})

    calls = audit.store.audit[0].tool_calls
    assert len(calls) == 1
    assert calls[0]["tool"] == "parallel_search"
    assert calls[0]["args"]["locale"] == "pt-BR"
    assert calls[0]["result_count"] == 7
    assert calls[0]["duration_s"] >= 0
    assert calls[0]["ok"] is True


def test_a_failed_tool_call_is_still_recorded(audit):
    """A tool that failed is part of why a decision came out the way it did."""
    with pytest.raises(RuntimeError):
        with audit.tool("fetch_page", url="https://x.invalid"):
            raise RuntimeError("connection reset")

    audit.append({"actor": "triage", "subject_id": "f2"})
    call = audit.store.audit[0].tool_calls[0]
    assert call["ok"] is False
    assert "connection reset" in call["error"]


# ---------------------------------------------------- a gap means we did not run


def test_a_failed_run_still_leaves_a_row(audit):
    """If a sweep found nothing there is a row saying so. If a sweep never
    happened there is no row. Those must never look the same."""
    audit.append({
        "actor": "text_sweep", "subject_id": "sweep2",
        "ok": False, "fail_state": "degraded", "reason": "circuit breaker open",
    })
    e = audit.store.audit[0]
    assert e.inputs["ok"] is False
    assert e.inputs["fail_state"] == "degraded"
    assert "circuit breaker" in e.inputs["reason"]


def test_the_harness_writes_a_row_without_being_asked(audit):
    """The whole point of putting this in the harness: an agent author cannot
    forget to audit, because they never call it."""
    policy = HarnessPolicy(agent_name="demo", fail_state=FailState.AMBIGUOUS, output_schema=dict)
    h = Harness(policy, HarnessDeps(audit=audit, prompt_version="v9"))

    h.run(lambda hint: {"answer": 42}, subject_id="f3")

    assert len(audit.store.audit) == 1
    e = audit.store.audit[0]
    assert e.actor == "demo"
    assert e.prompt_version == "v9"
    assert e.subject_id == "f3"


def test_the_harness_audits_failures_too(audit):
    policy = HarnessPolicy(agent_name="demo", fail_state=FailState.UNVERIFIED)
    h = Harness(policy, HarnessDeps(audit=audit, prompt_version="v9"))

    def boom(hint):
        raise ValueError("bad input")

    result = h.run(boom, subject_id="f4")
    assert not result.ok
    assert len(audit.store.audit) == 1, "a failure must leave a trail, not vanish"
    assert audit.store.audit[0].inputs["fail_state"] == "unverified"


# ------------------------------------------------------------------- reading


def test_the_trail_for_one_finding_reads_oldest_first(audit):
    for i, actor in enumerate(("fetch_page", "triage", "reconciler")):
        audit.store.append_audit(AuditEvent(
            id=f"aud_{i}", ts=datetime(2026, 9, 9, 10, i, tzinfo=timezone.utc),
            actor=actor, subject_type="finding", subject_id="f5",
        ))
    audit.store.append_audit(AuditEvent(
        id="aud_other", ts=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
        actor="triage", subject_type="finding", subject_id="f_other",
    ))

    trail = audit.trail("f5")
    assert [e.actor for e in trail] == ["fetch_page", "triage", "reconciler"]
    assert all(e.subject_id == "f5" for e in trail), "another finding's rows must not leak in"


def test_tool_calls_are_not_reused_between_rows(audit):
    """Two agents in one sweep must not each claim the other's tool calls."""
    with audit.tool("parallel_search", query="a") as c:
        c.record(from_cache=False)
    audit.append({"actor": "sweep", "subject_id": "s1"})
    audit.append({"actor": "reconciler", "subject_id": "s1"})

    assert len(audit.store.audit[0].tool_calls) == 1
    assert audit.store.audit[1].tool_calls == []
