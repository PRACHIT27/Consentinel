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

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from consentinel.agents.consent_ingest import ConsentDraft, extract_consent, to_consent
from consentinel.agents.consent_ingest import PROMPT_VERSION
from consentinel.audit import FirestoreAudit
from consentinel.cache import FirestoreCache
from consentinel.harness.ports import HarnessDeps
from consentinel.model_armor import ModelArmor, describe as describe_armor
from consentinel.obs import JsonLogger, Metrics, tracer_for
from consentinel.store.base import Performer, Store
from consentinel.store.firestore_store import FirestoreStore
from web import security

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
    global _store, _audit, _cache
    _store = store
    _audit = None
    _cache = None


_audit: Optional[FirestoreAudit] = None
_cache: Optional[FirestoreCache] = None
_armor: Optional[ModelArmor] = None

# One logger, one metrics sink, one tracer for the process. The tracer picks
# Cloud Trace when a project is configured and an in-memory one otherwise, so a
# laptop needs no credentials and deploy needs no flag.
log = JsonLogger(service="consentinel-web")
metrics = Metrics(log)
tracer = tracer_for()


def get_cache() -> FirestoreCache:
    """Shared across containers on purpose. An in-process cache is useless when
    the next request lands on a different Cloud Run instance — and this one
    survives a redeploy, which is what lets DEMO_MODE record a video with no
    network."""
    global _cache
    if _cache is None:
        _cache = FirestoreCache(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            prefix=os.environ.get("CONSENTINEL_PREFIX", ""),
        )
    return _cache


def get_audit() -> FirestoreAudit:
    """The trail. Every model call the app makes leaves a row, so a verdict can
    always be questioned afterwards."""
    global _audit
    if _audit is None:
        _audit = FirestoreAudit(get_store(), subject_type="consent")
    return _audit


def get_armor() -> ModelArmor:
    """The Model Armor screen, shared for the life of the process.

    `HarnessDeps.armor` defaults to `NullArmor`, which returns a clean verdict
    forever — indistinguishable from a screen that ran and found nothing. Until
    this was passed in, WU-29 was real code that never executed on a single
    request.

    Cheap to hold: the client is constructed lazily on first screen, so an
    instance costs nothing on a laptop with no credentials.

    A contract PDF is user-supplied text, so it goes through the **inbound**
    template — which labels and never blocks. A missing template or an API
    failure resolves to `armor_unavailable` and the upload still works; only
    the outbound notice screen blocks on failure, and the app never drafts one.
    """
    global _armor
    if _armor is None:
        _armor = ModelArmor(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            audit=get_audit(),
        )
    return _armor


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


# The console theme stamps a verdict rather than printing it: a rotated,
# double-outlined block, the way a clearance department actually marks a file.
# Same plain-English labels; only the presentation differs.
#
# The words inside a stamp stay plain and lower-case: plain because a viewer
# should not have to translate "unauthorized", lower-case because that is how
# the design draws them.
VERDICT_STAMP = {
    "authorized": ("teal", "allowed"),
    "unauthorized": ("orange", "not allowed"),
    "ambiguous": ("slate", "unclear"),
    None: ("slate", "not checked"),
}

CLEARANCE_STAMP = {
    "cleared": ("teal", "fine to ship"),
    "blocked": ("orange", "blocked"),
    "unverified": ("slate", "unchecked"),
}


def grant_state(consent) -> tuple[str, str]:
    """Is this grant live, running out, or finished?

    The registry screen leads with this because an expired grant looks exactly
    like a valid one until someone checks the date — and a lapsed grant means
    every use under it is now unauthorised.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    end = consent.valid_to
    if end is None:
        return ("active", "no end date")
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end < now:
        return ("expired", "expired")
    if end - now < timedelta(days=90):
        return ("expiring", "expiring soon")
    return ("active", "active")


def hostname(url: str) -> str:
    """Just the host. A full URL wraps over three lines in a table cell and the
    part a reader needs is the site."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).netloc or url
    except Exception:
        return url


templates.env.filters["verdict_style"] = lambda v: style_for(VERDICT_STYLE, v)
templates.env.filters["clearance_style"] = lambda v: style_for(CLEARANCE_STYLE, v)
templates.env.filters["verdict_stamp"] = lambda v: style_for(VERDICT_STAMP, v)
templates.env.filters["clearance_stamp"] = lambda v: style_for(CLEARANCE_STAMP, v)
templates.env.filters["grant_state"] = grant_state
templates.env.filters["hostname"] = hostname
templates.env.filters["ev"] = enum_value


# --------------------------------------------------------------------- routes


@app.get("/_health")
def healthz() -> JSONResponse:
    """Our own liveness check. Named `_health` and not `healthz`,
    because Cloud Run's frontend intercepts `/healthz` and returns its own 404
    before the request ever reaches the app.

    It must not touch Firestore — a health check that
    depends on the database reports the app as dead when the database is merely
    slow, and then the container gets restarted for no reason."""
    return JSONResponse({"ok": True})


def _qs(k: Optional[str]) -> str:
    """The action key, carried between pages so the nav link survives a click."""
    return f"?k={k}" if k else ""


@app.get("/", response_class=HTMLResponse)
def overview(request: Request, k: Optional[str] = None):
    """The console front page.

    Every number here is counted from Firestore rather than written in. A
    dashboard with invented figures is worse than no dashboard: it is the first
    thing a viewer trusts and the first thing that makes them stop trusting the
    rest.
    """
    store = get_store()
    performers = {p.id: p for p in store.list_performers()}
    consents = [c for pid in performers for c in store.list_consents(pid)]
    findings = store.list_findings()
    assets = store.list_assets(os.environ.get("CONSENTINEL_DEMO_PRODUCTION", "prod_halcyon_nightfall"))

    live = [c for c in consents if grant_state(c)[0] != "expired"]
    open_findings = [f for f in findings if enum_value(f.verdict) == "unauthorized"]
    locales = {f.discovered_locale for f in findings if f.discovered_locale}

    stats = {
        "grants": len(live),
        "open_findings": len(open_findings),
        "assets_checked": sum(1 for a in assets if enum_value(a.clearance_state) != "unverified"),
        "languages": len({loc.split("-")[0] for loc in locales}) or 1,
    }

    # Recent activity mixes both directions, worst first, because a breach is
    # what a reader needs to see before anything else.
    activity = []
    for f in sorted(findings, key=lambda f: ({"unauthorized": 0, "ambiguous": 1}.get(enum_value(f.verdict), 2), f.id)):
        stamp, label = style_for(VERDICT_STAMP, f.verdict)
        performer = performers.get(f.performer_id)
        activity.append({
            "title": hostname(f.url),
            "subtitle": (f.modality and f"synthetic {enum_value(f.modality)} offering") or "web finding",
            "direction": "enforcement", "direction_class": "expiring",
            "detail": f"{performer.name if performer else f.performer_id}"
                      + (" — no matching grant" if not f.matched_consent_id else ""),
            "stamp": label, "stamp_class": stamp, "href": "/findings",
        })
    for a in assets:
        if enum_value(a.clearance_state) == "cleared":
            continue          # the front page leads with what needs attention
        stamp, label = style_for(CLEARANCE_STAMP, a.clearance_state)
        activity.append({
            "title": a.filename,
            "subtitle": f"{a.vendor or 'vendor not recorded'}, {a.shot_code or 'no shot code'}",
            "direction": "clearance", "direction_class": "active",
            "detail": a.reasoning or "",
            "stamp": label, "stamp_class": stamp, "href": "/clearance",
        })

    return templates.TemplateResponse(
        request,
        "overview.html",
        {"nav": "overview", "k": k or "", "q": _qs(k),
         "stats": stats, "activity": activity[:6]},
    )


@app.get("/registry", response_class=HTMLResponse)
def registry(request: Request, k: Optional[str] = None, saved: Optional[str] = None):
    store = get_store()
    rows = []
    for performer in store.list_performers():
        rows.append({"performer": performer, "consents": store.list_consents(performer.id)})
    return templates.TemplateResponse(
        request,
        "registry.html",
        {"rows": rows, "nav": "registry", "k": k or "", "q": _qs(k), "saved": saved},
    )


@app.get("/findings", response_class=HTMLResponse)
def findings(request: Request, k: Optional[str] = None):
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
            "k": k or "",
            "q": _qs(k),
        },
    )


@app.get("/clearance", response_class=HTMLResponse)
def clearance(request: Request, k: Optional[str] = None):
    store = get_store()
    production = os.environ.get("CONSENTINEL_DEMO_PRODUCTION", "prod_halcyon_nightfall")
    assets = sorted(store.list_assets(production), key=lambda a: a.id)

    performers = {p.id: p for p in store.list_performers()}
    consents: dict[str, object] = {}
    for pid in performers:
        for c in store.list_consents(pid):
            consents[c.id] = c

    blockers = [a for a in assets if enum_value(a.clearance_state) == "blocked"]
    unchecked = [a for a in assets if enum_value(a.clearance_state) == "unverified"]

    return templates.TemplateResponse(
        request,
        "clearance.html",
        {
            "production": production,
            "assets": assets,
            "performers": performers,
            "consents": consents,
            "blockers": blockers,
            "unchecked": unchecked,
            "nav": "clearance",
            "k": k or "",
            "q": _qs(k),
        },
    )


# ------------------------------------------------------- adding a permission slip
#
# The one place in the app that spends money and writes to the registry, so it
# is the one place gated by a key (see web/security.py). A wrong permission slip
# silently poisons every later answer, which is why the model's answer is shown
# for a person to confirm rather than saved straight away.


@app.get("/consents/new", response_class=HTMLResponse)
def consent_new(request: Request, k: Optional[str] = None):
    security.check(k)
    return templates.TemplateResponse(
        request, "consent_new.html", {"nav": "registry", "k": k or "", "q": _qs(k)}
    )


@app.post("/consents/extract", response_class=HTMLResponse)
async def consent_extract(
    request: Request,
    contract: UploadFile = File(...),
    k: Optional[str] = Form(None),
):
    """Read the PDF and show what came back. Nothing is saved here."""
    security.check(k)

    suffix = Path(contract.filename or "contract.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await contract.read())
        tmp_path = Path(tmp.name)

    try:
        result = extract_consent(
            tmp_path,
            deps=HarnessDeps(
                audit=get_audit(),
                cache=get_cache(),
                armor=get_armor(),
                tracer=tracer,
                metrics=metrics,
                prompt_version=PROMPT_VERSION,
            ),
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    trace_id, span_id = tracer.current_ids()
    if not result.ok:
        log.warning("could not read a contract",
                    filename=contract.filename, fail_state=enum_value(result.fail_state),
                    reason=result.reason, trace_id=trace_id, span_id=span_id)
        metrics.counter("extraction.validation_failures",
                        reason=enum_value(result.fail_state) or "unknown")
        return templates.TemplateResponse(
            request,
            "consent_new.html",
            {
                "nav": "registry",
                "k": k or "",
                "q": _qs(k),
                "error": result.reason,
                "fail_state": enum_value(result.fail_state),
            },
            status_code=422,
        )

    log.info("read a contract",
             filename=contract.filename, pages=result.value.page_count,
             citations=len(result.value.citations), dropped=len(result.value.dropped),
             from_cache=result.from_cache, cache_age_s=result.cache_age_s,
             duration_s=round(result.duration_s, 2),
             injection_suspected=result.injection_suspected,
             # So "the screen found nothing" is distinguishable from "no screen
             # ran". `NullArmor` returns clean verdicts forever and looks
             # identical to a clean page in every other field.
             armor=describe_armor(get_armor()),
             trace_id=trace_id, span_id=span_id)
    if result.value.dropped:
        for d in result.value.dropped:
            metrics.counter("extraction.validation_failures", reason=d["why"])

    return templates.TemplateResponse(
        request,
        "consent_confirm.html",
        {
            "nav": "registry",
            "k": k or "",
            "q": _qs(k),
            "draft": result.value,
            "filename": contract.filename,
            "result": result,
        },
    )


@app.post("/consents/save")
def consent_save(
    k: Optional[str] = Form(None),
    performer_name: str = Form(...),
    licensee: str = Form(...),
    permitted_uses: list[str] = Form(default=[]),
    territories: str = Form(""),
    valid_from: str = Form(""),
    valid_to: str = Form(""),
    compensation_trigger: str = Form(""),
    citations_json: str = Form("[]"),
    source_doc_ref: str = Form(""),
):
    """Write the confirmed slip. Reuses an existing performer by name so a second
    contract for the same person does not create a duplicate."""
    security.check(k)
    store = get_store()

    slug = re.sub(r"[^a-z0-9]+", "_", performer_name.strip().lower()).strip("_")
    performer = next(
        (p for p in store.list_performers() if p.name.strip().lower() == performer_name.strip().lower()),
        None,
    )
    if performer is None:
        performer = store.upsert_performer(Performer(id=f"perf_{slug}", name=performer_name.strip()))

    draft = ConsentDraft(
        performer_name=performer_name.strip(),
        licensee=licensee.strip(),
        permitted_uses=list(permitted_uses),
        territories=[t.strip().upper() for t in territories.split(",") if t.strip()],
        valid_from=valid_from.strip(),
        valid_to=valid_to.strip(),
        compensation_trigger=compensation_trigger.strip(),
        citations=json.loads(citations_json or "[]"),
        source_doc_ref=source_doc_ref or None,
    )

    consent = to_consent(
        draft,
        consent_id=f"cons_{slug}_{re.sub(r'[^a-z0-9]+', '', licensee.lower())[:12]}",
        performer_id=performer.id,
    )
    store.upsert_consent(consent)

    sep = f"?k={k}&" if k else "?"
    return RedirectResponse(url=f"/registry{sep}saved={consent.id}", status_code=303)
