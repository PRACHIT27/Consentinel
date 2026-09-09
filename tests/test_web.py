"""The three screens.

These run against a fake in-memory store, so no network and no Google Cloud
login is needed. The point is the rendering rules, not the database — that is
covered in test_firestore_store.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from consentinel.store.base import (
    Asset,
    AuditEvent,
    ClearanceState,
    Consent,
    DiscoveredVia,
    Dossier,
    Finding,
    FindingStatus,
    Modality,
    Performer,
    PermittedUse,
    Store,
    Verdict,
)
from web import app as webapp

UTC = timezone.utc


class FakeStore(Store):
    """Just enough Store to render three pages."""

    def __init__(self):
        self.performers: list[Performer] = []
        self.consents: list[Consent] = []
        self.findings: list[Finding] = []
        self.assets: list[Asset] = []

    def upsert_performer(self, p): self.performers.append(p); return p
    def get_performer(self, pid): return next((p for p in self.performers if p.id == pid), None)
    def list_performers(self): return list(self.performers)

    def upsert_consent(self, c): self.consents.append(c); return c
    def list_consents(self, performer_id):
        return [c for c in self.consents if c.performer_id == performer_id]

    def upsert_finding(self, f): self.findings.append(f); return f
    def get_finding(self, fid): return next((f for f in self.findings if f.id == fid), None)
    def list_findings(self, performer_id=None, verdict=None, status=None): return list(self.findings)

    def upsert_asset(self, a): self.assets.append(a); return a
    def list_assets(self, production_id, state=None):
        return [a for a in self.assets if a.production_id == production_id]

    def put_dossier(self, d: Dossier): return d
    def get_dossier(self, finding_id): return None
    def append_audit(self, e: AuditEvent): return None
    def list_audit(self, subject_type=None, subject_id=None): return []


def _finding(fid, verdict, *, quote="a quote", territories=("US",), locale="en-US"):
    return Finding(
        id=fid, performer_id="p1", url=f"https://example-{fid}.invalid/x",
        url_hash=fid, discovered_via=DiscoveredVia.TEXT, discovered_locale=locale,
        target_territories=list(territories), modality=Modality.VOICE,
        is_commercial=True, evidence_quote=quote, confidence=0.9,
        verdict=verdict, reasoning=f"because {fid}", status=FindingStatus.NEW,
    )


@pytest.fixture
def client():
    store = FakeStore()
    store.upsert_performer(Performer(id="p1", name="Mira Vance", aliases=["M. Vance"]))
    store.upsert_consent(Consent(
        id="c1", performer_id="p1", licensee="Halcyon Pictures",
        permitted_uses=[PermittedUse.VOICE_SYNTH], territories=["US", "CA"],
        valid_from=datetime(2026, 1, 1, tzinfo=UTC), valid_to=datetime(2028, 12, 31, tzinfo=UTC),
        clause_citations=[{"quote": "Producer may generate synthetic voice performances.", "page": 4}],
    ))
    store.upsert_finding(_finding("f_allowed", Verdict.AUTHORIZED))
    store.upsert_finding(_finding("f_unclear", Verdict.AMBIGUOUS))
    store.upsert_finding(_finding("f_breach", Verdict.UNAUTHORIZED, territories=("BR",), locale="pt-BR"))
    store.upsert_asset(Asset(
        id="a_ok", production_id="prod_x", filename="ok.wav", shot_code="S1",
        synthetic="yes", detected_modality=Modality.VOICE,
        clearance_state=ClearanceState.CLEARED, matched_consent_id="c1",
        performer_id="p1", vendor="Northlight", reasoning="covered",
    ))
    store.upsert_asset(Asset(
        id="a_no", production_id="prod_x", filename="no.exr", shot_code="S2",
        synthetic="yes", detected_modality=Modality.FACE,
        clearance_state=ClearanceState.BLOCKED, performer_id="p1",
        vendor="Cyan Alley", reasoning="face not permitted",
    ))
    store.upsert_asset(Asset(
        id="a_unk", production_id="prod_x", filename="unk.exr",
        clearance_state=ClearanceState.UNVERIFIED, reasoning="no paperwork",
    ))
    webapp.set_store(store)
    webapp.os.environ["CONSENTINEL_DEMO_PRODUCTION"] = "prod_x"
    yield TestClient(webapp.app)
    webapp.set_store(None)


def test_all_three_pages_load(client):
    for path in ("/", "/findings", "/clearance"):
        assert client.get(path).status_code == 200, path


def test_health_check_does_not_touch_the_database(client):
    """Cloud Run pings this. If it needed the database, a slow database would
    look like a dead app and the container would be restarted for nothing."""
    webapp.set_store(None)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_registry_shows_the_contract_sentence(client):
    body = client.get("/").text
    assert "Mira Vance" in body
    assert "M. Vance" in body                       # the alias matters for sweeps
    assert "Producer may generate synthetic voice" in body
    assert "page 4" in body


def test_findings_lead_with_breaches(client):
    """A judge sees the top of the page. Alphabetical order would put 'unclear'
    first and bury the thing the screen exists to show."""
    body = client.get("/findings").text
    first_breach = body.index("Not allowed")
    first_allowed = body.index(">Allowed<")
    assert first_breach < first_allowed


def test_verdicts_are_shown_in_plain_words(client):
    body = client.get("/findings").text
    assert "Not allowed" in body and "Unclear" in body
    assert "unauthorized" not in body               # raw values never reach the screen
    assert "ambiguous" not in body


def test_clearance_counts_the_blockers(client):
    body = client.get("/clearance").text
    assert "1 clip cannot ship" in body
    assert "1 clip unchecked" in body
    assert "Fine to ship" in body and "Blocked" in body and "Unchecked" in body


def test_unchecked_is_not_presented_as_fine(client):
    """An asset with no paperwork must never read as approved."""
    body = client.get("/clearance").text
    card = next(c for c in body.split("<article") if "unk.exr" in c)
    assert "Unchecked" in card
    assert "Fine to ship" not in card


def test_text_from_someone_elses_website_cannot_run_as_code(client):
    """We display text lifted from pages we do not control. If it were rendered
    as markup, a stranger's page could run script inside our app."""
    store = FakeStore()
    store.upsert_performer(Performer(id="p1", name="Mira Vance"))
    store.upsert_finding(_finding(
        "f_evil", Verdict.UNAUTHORIZED,
        quote='<script>alert("xss")</script><img src=x onerror=alert(1)>',
    ))
    webapp.set_store(store)

    body = TestClient(webapp.app).get("/findings").text
    # No tag can form: the angle brackets are escaped, so the browser sees text.
    assert "<script>alert" not in body
    assert "<img src=x" not in body
    assert "&lt;script&gt;alert" in body            # shown as words, harmlessly
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
