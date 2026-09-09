"""The web app.

Runs against a stand-in store, so none of this needs the network. The point is
the app's own behaviour: what it renders, what it escapes, and what it refuses.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from consentinel.store.base import (
    Asset,
    ClearanceState,
    Consent,
    DiscoveredVia,
    Finding,
    FindingStatus,
    Performer,
    PermittedUse,
    Verdict,
)
from web import app as webapp
from web import security


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class FakeStore:
    """Just enough Store for the screens."""

    def __init__(self):
        self.performers = [Performer(id="p1", name="Mira Vance", aliases=["M. Vance"])]
        self.consents = [Consent(
            id="c1", performer_id="p1", licensee="Halcyon Pictures",
            permitted_uses=[PermittedUse.VOICE_SYNTH, PermittedUse.ARCHIVAL_REUSE],
            territories=["US", "CA"], valid_from=dt(2026, 1, 1), valid_to=dt(2028, 12, 31),
            compensation_trigger="per-title fee",
            clause_citations=[{"quote": "Producer may generate synthetic voice performances.", "page": 4}],
        )]
        self.findings = [
            Finding(id="f1", performer_id="p1", url="https://a.invalid/x", url_hash="h1",
                    discovered_via=DiscoveredVia.TEXT, discovered_locale="en-US",
                    target_territories=["US"], verdict=Verdict.UNAUTHORIZED,
                    evidence_quote="AI voice model of Mira Vance", reasoning="no grant",
                    is_commercial=True, confidence=0.9, status=FindingStatus.NEW),
            Finding(id="f2", performer_id="p1", url="https://b.invalid/y", url_hash="h2",
                    discovered_via=DiscoveredVia.TEXT, discovered_locale="en-US",
                    target_territories=["US", "CA"], verdict=Verdict.AUTHORIZED,
                    evidence_quote="Halcyon confirmed synthetic pickups", reasoning="covered",
                    matched_consent_id="c1", confidence=0.8, status=FindingStatus.REVIEWED),
            Finding(id="f3", performer_id="p1", url="https://c.invalid/z", url_hash="h3",
                    discovered_via=DiscoveredVia.IMAGE, discovered_locale="en-US",
                    verdict=Verdict.AMBIGUOUS, reasoning="below threshold", confidence=0.4,
                    status=FindingStatus.NEW),
        ]
        self.assets = [
            Asset(id="a1", production_id="prod", filename="v.wav", clearance_state=ClearanceState.CLEARED,
                  matched_consent_id="c1", reasoning="covered", synthetic="yes", vendor="Northlight"),
            Asset(id="a2", production_id="prod", filename="f.exr", clearance_state=ClearanceState.BLOCKED,
                  reasoning="visual likeness withheld", synthetic="yes", vendor="Cyan Alley"),
            Asset(id="a3", production_id="prod", filename="p.exr", clearance_state=ClearanceState.UNVERIFIED,
                  reasoning="no paperwork", synthetic="unknown"),
        ]
        self.saved: list[Consent] = []

    def list_performers(self): return list(self.performers)
    def upsert_asset(self, a): self.assets = [x for x in self.assets if x.id != a.id] + [a]; return a
    def list_consents(self, performer_id): return [c for c in self.consents if c.performer_id == performer_id]
    def list_findings(self, **kw): return list(self.findings)
    def list_assets(self, production_id, state=None): return list(self.assets)
    def upsert_performer(self, p): self.performers.append(p); return p
    def upsert_consent(self, c): self.saved.append(c); self.consents.append(c); return c


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(security.ENV_VAR, raising=False)
    store = FakeStore()
    webapp.set_store(store)
    c = TestClient(webapp.app)
    c.store = store
    yield c
    webapp.set_store(None)


# ------------------------------------------------------------------- screens


def test_health_does_not_touch_the_database(client):
    """A health check that depends on Firestore reports the app as dead when the
    database is merely slow, and the container gets restarted for nothing."""
    webapp.set_store(None)
    r = client.get("/_health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_the_front_page_counts_from_the_store_rather_than_inventing_numbers(client):
    """A dashboard with made-up figures is worse than no dashboard: it is the
    first thing a viewer trusts and the first thing that breaks that trust."""
    body = client.get("/").text
    assert "One registry, two directions." in body
    assert "Mira Vance" in body                 # the open finding names its performer
    assert "a.invalid" in body                  # ...and the site it was found on
    assert "f.exr" in body                      # the blocked clip, from the other direction
    assert "v.wav" not in body, "a cleared clip needs no attention, so it stays off the front page"


def test_registry_shows_the_grant_and_its_quote(client):
    body = client.get("/registry").text
    assert "Mira Vance" in body
    assert "Halcyon Pictures" in body
    assert "Producer may generate synthetic voice performances." in body
    assert "page 4" in body


def test_findings_lead_with_the_breaches(client):
    """Sorting alphabetically would put "ambiguous" first and bury the thing the
    page exists to show."""
    body = client.get("/findings").text
    assert body.index("not allowed") < body.index("unclear")


def test_verdicts_are_shown_in_plain_words(client):
    """A judge watching a video should not have to translate "unauthorized"."""
    body = client.get("/findings").text
    assert "not allowed" in body and "unauthorized" not in body


def test_clearance_names_what_cannot_ship(client):
    body = client.get("/clearance").text
    assert "blocked" in body
    assert "cannot ship" in body
    assert "unchecked" in body
    assert "Nothing in the registry permits this" in body


def test_every_row_carries_its_own_detail(client):
    """The detail panel is filled by moving nodes the server already rendered.
    If a row shipped without its detail block the panel would open empty, and
    nothing else on the page would look wrong."""
    for path in ("/registry", "/findings", "/clearance"):
        body = client.get(path).text
        rows = body.count("data-drawer-title=")
        assert rows > 0, path
        assert body.count("data-drawer-body") == rows, path
        assert 'src="/static/console.js"' in body


# ------------------------------------------------------------------ escaping


def test_text_from_someone_elses_site_is_escaped(client):
    """We render content from pages we do not control. Treating any of it as
    markup would let a stranger's page run script inside our own app."""
    client.store.findings[0].evidence_quote = '<script>alert("xss")</script>'
    client.store.findings[0].url = 'https://evil.invalid/"><script>alert(1)</script>'
    body = client.get("/findings").text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


# ------------------------------------------------------------------- the gate


def test_actions_are_open_when_no_key_is_configured(client):
    """Right default for a laptop, where the app is not reachable from outside."""
    assert security.actions_are_open()
    assert client.get("/consents/new").status_code == 200


def test_actions_are_refused_without_the_key(monkeypatch):
    """The read screens are public so a judge can open them, but reading a
    contract costs a Gemini call and a sweep costs Parallel calls. On a public
    address with no gate, a crawler can drain the quota we need for the demo."""
    monkeypatch.setenv(security.ENV_VAR, "s3cret")
    webapp.set_store(FakeStore())
    c = TestClient(webapp.app)

    assert c.get("/").status_code == 200                      # reads stay open
    assert c.get("/findings").status_code == 200
    assert c.get("/consents/new").status_code == 403           # actions do not
    assert c.get("/consents/new?k=wrong").status_code == 403
    assert c.get("/consents/new?k=s3cret").status_code == 200
    webapp.set_store(None)


def test_saving_a_slip_writes_it_and_reuses_the_performer(client):
    r = client.post("/consents/save", data={
        "performer_name": "Mira Vance",
        "licensee": "Halcyon Pictures",
        "permitted_uses": ["voice_synth"],
        "territories": "US, CA",
        "valid_from": "2026-01-01",
        "valid_to": "2028-12-31",
        "compensation_trigger": "per-title fee",
        "citations_json": '[{"quote": "q", "page": 4}]',
    }, follow_redirects=False)

    assert r.status_code == 303
    assert len(client.store.saved) == 1
    saved = client.store.saved[0]
    assert saved.performer_id == "p1", "should reuse the existing performer, not duplicate them"
    assert saved.territories == ["US", "CA"]
    assert saved.permitted_uses == [PermittedUse.VOICE_SYNTH]


def test_checking_a_clip_writes_the_answer_and_returns_to_the_screen(client, monkeypatch):
    """The inward direction, end to end through the app: a clip plus a delivery
    note goes in, a row with a verdict comes out."""
    from consentinel.agents.clearance import ClearanceOutcome
    from consentinel.store.base import ClearanceState

    monkeypatch.setattr(
        webapp, "check_asset",
        lambda **kw: ClearanceOutcome(
            state=ClearanceState.BLOCKED, reasoning="nothing permits this",
            content_hash="a" * 64, declared_modality="face"),
    )

    r = client.post(
        "/clearance/check",
        data={"performer_id": "p1", "licensee": "Halcyon Pictures", "modality": "face",
              "territories": "US", "vendor": "Cyan Alley", "synthetic": "yes"},
        files={"clip": ("plate.png", b"\x89PNG", "image/png")},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert "/clearance" in r.headers["location"]
    written = [a for a in client.store.assets if a.filename == "plate.png"]
    assert len(written) == 1, "one row per delivery, keyed on the bytes and the shot"
    assert written[0].clearance_state == ClearanceState.BLOCKED
    assert written[0].vendor == "Cyan Alley"


def test_a_clip_check_needs_the_key(monkeypatch):
    """It costs a Gemini call, so it is gated like every other action."""
    monkeypatch.setenv(security.ENV_VAR, "s3cret")
    webapp.set_store(FakeStore())
    c = TestClient(webapp.app)
    r = c.post("/clearance/check",
               data={"performer_id": "p1", "licensee": "X", "modality": "voice"},
               files={"clip": ("a.wav", b"\x00", "audio/wav")})
    assert r.status_code == 403
    webapp.set_store(None)


def test_an_unreadable_contract_says_so_rather_than_saving_nothing(client, monkeypatch):
    """A scan has no selectable text. The screen must explain that instead of
    silently producing an empty permission slip."""
    from consentinel.harness.policy import FailState
    from consentinel.harness.result import HarnessResult

    monkeypatch.setattr(
        webapp, "extract_consent",
        lambda *a, **kw: HarnessResult(ok=False, fail_state=FailState.UNVERIFIED,
                                       reason="no readable text in the PDF - it may be a scan, which needs OCR first"),
    )
    r = client.post("/consents/extract", files={"contract": ("scan.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422
    assert "OCR" in r.text
    assert client.store.saved == []


def test_a_gated_action_says_it_exists_rather_than_vanishing(monkeypatch):
    """Without the key the upload forms are off. They must still be *described*.

    An invisible feature reads as a missing one: the first person to open the
    public link and conclude the product cannot check a clip was Prachit, on
    the day of the deadline.
    """
    monkeypatch.setenv(security.ENV_VAR, "s3cret")
    webapp.set_store(FakeStore())
    c = TestClient(webapp.app)

    clearance = c.get("/clearance").text
    assert "needs the team key" in clearance
    assert "Check against the registry" not in clearance, "the form itself stays off"

    registry = c.get("/registry").text
    assert "switched off on this link" in registry

    webapp.set_store(None)
