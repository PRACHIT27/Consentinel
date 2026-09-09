"""`web_risk_check` — contract v2.0.0, DESIGN Part III §1.

The behaviour that matters most is the one safety checks usually get wrong:
**this one fails closed.** If the check cannot be completed, the address is
reported unsafe. Two tests assert that directly, because it is the kind of
property a later refactor silently inverts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from consentinel.harness import HarnessDeps, MemoryAudit, MemoryMetrics
from consentinel.harness.ports import CacheHit
from consentinel.tools.contracts import UrlRisk
from consentinel.tools.web_risk import (
    CHECK_FAILED,
    THREAT_TYPE_NAMES,
    WebRiskCheck,
    is_enabled,
)

URL = "https://loja.example/mira-voz"


class FakeThreat:
    def __init__(self, *names: str) -> None:
        self.threat_types = [type("T", (), {"name": n})() for n in names]


class FakeResponse:
    def __init__(self, threat: Optional[FakeThreat] = None) -> None:
        self.threat = threat


class FakeRiskClient:
    """Records calls; returns a canned answer or raises."""

    def __init__(self, threat: Optional[FakeThreat] = None,
                 error: Optional[BaseException] = None) -> None:
        self.threat = threat
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search_uris(self, *, uri: str, threat_types: Any) -> FakeResponse:
        self.calls.append({"uri": uri, "threat_types": threat_types})
        if self.error:
            raise self.error
        return FakeResponse(self.threat)


class Cache(dict):
    def get(self, key):
        from datetime import datetime, timezone
        return (CacheHit(value=self[key], fetched_at=datetime.now(timezone.utc))
                if key in self else None)

    def put(self, key, value, ttl):
        self[key] = value


def checker(client: Optional[FakeRiskClient] = None, **kw: Any
            ) -> tuple[WebRiskCheck, FakeRiskClient, MemoryAudit]:
    client = client or FakeRiskClient()
    audit = MemoryAudit()
    kw.setdefault("sleep", lambda _s: None)
    c = WebRiskCheck(deps=HarnessDeps(audit=audit, metrics=MemoryMetrics(),
                                      **kw.pop("deps_kw", {})),
                     client=client, **kw)
    return c, client, audit


# --------------------------------------------------------------- the answer

def test_a_clean_address_comes_back_safe():
    c, client, _ = checker()

    risk = c.check(URL)

    assert isinstance(risk, UrlRisk)
    assert risk.safe is True
    assert risk.threats == []
    assert risk.checked_at is not None
    assert client.calls[0]["uri"] == URL


def test_a_listed_address_comes_back_unsafe_with_the_threat_names():
    """The names travel so the screen can say why we skipped it."""
    c, _, _ = checker(FakeRiskClient(FakeThreat("MALWARE", "SOCIAL_ENGINEERING")))

    risk = c.check(URL)

    assert risk.safe is False
    assert risk.threats == ["MALWARE", "SOCIAL_ENGINEERING"]


def test_all_four_threat_types_are_asked_about():
    c, client, _ = checker()

    c.check(URL)

    assert len(client.calls[0]["threat_types"]) == len(THREAT_TYPE_NAMES) == 4


# ------------------------------------------------------------- fails closed

def test_a_failing_check_reports_unsafe():
    """Contract v2.0.0: if the check itself fails, treat the address as unsafe
    and skip it. One missed listing costs nothing; opening a malware page
    costs more."""
    c, _, _ = checker(FakeRiskClient(error=RuntimeError("quota exceeded")))

    risk = c.check(URL)

    assert risk.safe is False
    assert risk.threats == [CHECK_FAILED]


def test_the_check_never_raises_out():
    """A safety check that throws is a safety check somebody wraps in
    try/except and ignores."""
    c, _, _ = checker(FakeRiskClient(error=KeyError("boom")))

    assert c.check(URL).safe is False


def test_check_failed_is_distinguishable_from_a_real_threat():
    failed, _, _ = checker(FakeRiskClient(error=RuntimeError("down")))
    listed, _, _ = checker(FakeRiskClient(FakeThreat("MALWARE")))

    assert failed.check(URL).threats == [CHECK_FAILED]
    assert listed.check(URL).threats == ["MALWARE"]


def test_an_empty_url_is_refused_without_calling_the_api():
    c, client, _ = checker()

    risk = c.check("   ")

    assert risk.safe is False
    assert client.calls == []


def test_the_refusal_is_logged_loudly(caplog):
    c, _, _ = checker(FakeRiskClient(error=RuntimeError("api not enabled")))

    with caplog.at_level(logging.WARNING, logger="consentinel.web_risk"):
        c.check(URL)

    line = next(r.getMessage() for r in caplog.records if "safe=False" in r.getMessage())
    assert "failing closed" in line
    assert "api not enabled" in line


# ------------------------------------------------------------------ caching

def test_the_same_address_is_only_checked_once_per_ttl():
    c, client, _ = checker(deps_kw={"cache": Cache()})

    c.check(URL)
    second = c.check(URL)

    assert len(client.calls) == 1
    assert second.safe is True


def test_a_different_address_is_checked_again():
    c, client, _ = checker(deps_kw={"cache": Cache()})

    c.check(URL)
    c.check("https://other.example/x")

    assert len(client.calls) == 2


# ------------------------------------------------------------------- audit

def test_every_check_writes_an_audit_row():
    c, _, audit = checker(FakeRiskClient(FakeThreat("UNWANTED_SOFTWARE")))

    c.check(URL, subject_id="perf_mira")

    row = next(e for e in audit.events if e.get("event") == "tool_call")
    assert row["url"] == URL
    assert row["safe"] is False
    assert row["threats"] == ["UNWANTED_SOFTWARE"]
    assert row["subject_id"] == "perf_mira"
    assert row["api"] == "webrisk.searchUris"


def test_a_failed_check_is_audited_as_not_ok():
    c, _, audit = checker(FakeRiskClient(error=RuntimeError("down")))

    c.check(URL)

    row = next(e for e in audit.events if e.get("event") == "tool_call")
    assert row["ok"] is False
    assert "down" in (row["reason"] or "")


# ----------------------------------------------------------------- the flag

def test_the_check_is_on_unless_explicitly_disabled(monkeypatch):
    monkeypatch.delenv("WEB_RISK_ENABLED", raising=False)
    assert is_enabled() is True
    for off in ("false", "FALSE", "0", "no"):
        monkeypatch.setenv("WEB_RISK_ENABLED", off)
        assert is_enabled() is False
    monkeypatch.setenv("WEB_RISK_ENABLED", "true")
    assert is_enabled() is True


def test_contract_signature_is_unchanged():
    import inspect
    from importlib import import_module

    from consentinel.tools import contracts
    module = import_module("consentinel.tools.web_risk")

    assert list(inspect.signature(module.web_risk_check).parameters) == \
           list(inspect.signature(contracts.web_risk_check).parameters)
