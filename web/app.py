"""The Consentinel web app.

Three screens, all reading from Firestore:

    /            Registry   - performers and the permission slips we hold
    /findings    Findings   - what the sweep found, and whether it is allowed
    /clearance   Clearance  - our own film clips: fine to ship, blocked, or unchecked

Deliberately plain. No login, no accounts, no build step. Everything a judge
needs to see is on three pages that render on the server.

One rule matters more than the others here: **every piece of text that came
from someone else's website is escaped before it is shown.** Jinja does that by
default, and nothing in the templates uses `| safe` on borrowed text. We display
content from pages we do not control, so treating any of it as markup would let
a stranger's page run script inside our own app.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from consentinel.store.base import Store
from consentinel.store.firestore_store import FirestoreStore

# Read .env if there is one. Cloud Run sets real environment variables and has
# no .env file, so this only ever affects a laptop.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

app = FastAPI(title="Consentinel", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

_store: Optional[Store] = None


def get_store() -> Store:
    """One store for the process. Firestore clients are safe to reuse and
    expensive to build, and Cloud Run keeps the container warm between
    requests."""
    global _store
    if _store is None:
        _store = FirestoreStore(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            prefix=os.environ.get("CONSENTINEL_PREFIX", ""),
        )
    return _store


def set_store(store: Store) -> None:
    """Used by tests to swap in a fake so they do not need the network."""
    global _store
    _store = store


# --------------------------------------------------------------- presentation

# Verdict and clearance values map to a colour and a plain-English label.
# The label is what a viewer reads; the raw value never reaches the screen,
# because "unauthorized" and "not allowed" are not equally clear to a judge
# watching a video.
VERDICT_STYLE = {
    "authorized": ("ok", "Allowed"),
    "unauthorized": ("bad", "Not allowed"),
    "ambiguous": ("warn", "Unclear"),
    None: ("muted", "Not checked yet"),
}

CLEARANCE_STYLE = {
    "cleared": ("ok", "Fine to ship"),
    "blocked": ("bad", "Blocked"),
    "unverified": ("muted", "Unchecked"),
}


def style_for(mapping, value) -> tuple[str, str]:
    key = getattr(value, "value", value)
    return mapping.get(key, ("muted", str(key or "—")))


def enum_value(value):
    return getattr(value, "value", value)


templates.env.filters["verdict_style"] = lambda v: style_for(VERDICT_STYLE, v)
templates.env.filters["clearance_style"] = lambda v: style_for(CLEARANCE_STYLE, v)
templates.env.filters["ev"] = enum_value


# --------------------------------------------------------------------- routes


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Cloud Run pings this. It must not touch Firestore — a health check that
    depends on the database reports the app as dead when the database is merely
    slow, and then the container gets restarted for no reason."""
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
def registry(request: Request):
    store = get_store()
    rows = []
    for performer in store.list_performers():
        rows.append({"performer": performer, "consents": store.list_consents(performer.id)})
    return templates.TemplateResponse(
        request, "registry.html", {"rows": rows, "nav": "registry"}
    )


@app.get("/findings", response_class=HTMLResponse)
def findings(request: Request):
    store = get_store()
    performers = {p.id: p for p in store.list_performers()}
    # Lead with the breaches. Sorting alphabetically would put "ambiguous"
    # first, which buries the thing the page exists to show.
    order = {"unauthorized": 0, "ambiguous": 1, "authorized": 2}
    items = sorted(
        store.list_findings(),
        key=lambda f: (order.get(enum_value(f.verdict), 3), f.id),
    )

    # The grant a finding was matched against, so the screen can show the exact
    # clause a breach relies on rather than only the verdict.
    consents: dict[str, object] = {}
    for pid in performers:
        for c in store.list_consents(pid):
            consents[c.id] = c

    counts = {"authorized": 0, "unauthorized": 0, "ambiguous": 0}
    for f in items:
        key = enum_value(f.verdict)
        if key in counts:
            counts[key] += 1

    return templates.TemplateResponse(
        request,
        "findings.html",
        {
            "findings": items,
            "performers": performers,
            "consents": consents,
            "counts": counts,
            "nav": "findings",
        },
    )


@app.get("/clearance", response_class=HTMLResponse)
def clearance(request: Request):
    store = get_store()
    production = os.environ.get("CONSENTINEL_DEMO_PRODUCTION", "prod_halcyon_nightfall")
    assets = sorted(store.list_assets(production), key=lambda a: a.id)

    consents: dict[str, object] = {}
    for p in store.list_performers():
        for c in store.list_consents(p.id):
            consents[c.id] = c

    blockers = [a for a in assets if enum_value(a.clearance_state) == "blocked"]
    unchecked = [a for a in assets if enum_value(a.clearance_state) == "unverified"]

    return templates.TemplateResponse(
        request,
        "clearance.html",
        {
            "production": production,
            "assets": assets,
            "consents": consents,
            "blockers": blockers,
            "unchecked": unchecked,
            "nav": "clearance",
        },
    )
