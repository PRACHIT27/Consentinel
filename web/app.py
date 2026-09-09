"""The Consentinel web app.

Four screens, all reading from Firestore:

    /            Overview   - the counts, both directions, recent activity
    /registry    Registry   - performers and the permission slips we hold
    /findings    Findings   - what the sweep found, and whether it is allowed
    /clearance   Clearance  - our own film clips: fine to ship, blocked, or unchecked

Two of them also act, and both cost a model call, so both are gated by a shared
key (`web/security.py`): `POST /consents/extract` reads a contract, and
`POST /clearance/check` checks a clip. Reads stay open so a judge can look.

Deliberately plain. No login, no accounts, no build step. Everything a judge
needs to see renders on the server.

One rule matters more than the others here: **every piece of text that came
from someone else's website is escaped before it is shown.** Jinja does that by
default, and nothing in the templates uses `| safe` on borrowed text. We display
content from pages we do not control, so treating any of it as markup would let
a stranger's page run script inside our own app.
"""

from __future__ import annotations

import hashlib
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

from consentinel.agents.clearance import Declaration, check_asset, to_asset
from consentinel.agents.clearance import PROMPT_VERSION as CLEARANCE_PROMPT_VERSION
from consentinel.agents.consent_ingest import ConsentDraft, extract_consent, to_consent
from consentinel.agents.consent_ingest import PROMPT_VERSION
from consentinel.agents.query_planner import DEFAULT_LOCALES
from consentinel.audit import FirestoreAudit
from consentinel.cache import FirestoreCache
from consentinel.harness.ports import HarnessDeps
from consentinel.model_armor import ModelArmor, describe as describe_armor
from consentinel.obs import JsonLogger, Metrics, tracer_for
from consentinel.store.base import Performer, Store
from consentinel.store.firestore_store import FirestoreStore
from consentinel.agents.dossier_writer import DossierWriter, build_bundle
from consentinel.store.base import Dossier as StoredDossier
from consentinel.store.base import FindingStatus
from consentinel.sweep import run as sweep_run
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


def _can_act(k: Optional[str]) -> bool:
    """Should this screen offer the buttons that spend money?

    Yes when no token is configured — a laptop — and yes when the supplied key
    matches. Never merely because a key was supplied.
    """
    return security.allows(k)


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

    stats = {
        "grants": len(live),
        "open_findings": len(open_findings),
        "assets_checked": sum(1 for a in assets if enum_value(a.clearance_state) != "unverified"),
        # The sweep's own locale list, not a count of what the findings happen
        # to contain. Counting findings made this tile read "2" while the
        # planner searches five languages, which understates the thing and
        # invites the question of which number is real.
        "languages": len({loc.language for loc in DEFAULT_LOCALES}),
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
        {"nav": "overview", "k": k or "", "q": _qs(k), "can_act": _can_act(k),
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
        {"rows": rows, "nav": "registry", "k": k or "", "q": _qs(k),
         "can_act": _can_act(k), "saved": saved},
    )


@app.get("/findings", response_class=HTMLResponse)
def findings(request: Request, k: Optional[str] = None,
             swept: Optional[int] = None, withheld: Optional[int] = None,
             searched: Optional[int] = None, live: Optional[int] = None,
             cached: Optional[int] = None):
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
            "can_act": _can_act(k),
            "swept": swept,
            "withheld": withheld or 0,
            "searched": searched or 0,
            "live": live or 0,
            "cached": cached or 0,
        },
    )


@app.get("/clearance", response_class=HTMLResponse)
def clearance(request: Request, k: Optional[str] = None, checked: Optional[str] = None):
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
            "can_act": _can_act(k),
            # For the submit form: which studios hold a grant. Offering a
            # free-text performer instead of the ones on file would let a typo
            # produce a permanent `unverified` nobody can explain.
            "licensees": sorted({c.licensee for c in consents.values() if c.licensee}),
            "checked": checked,
            "just_checked": next((a for a in assets if a.id == checked), None),
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
        request,
        "consent_new.html",
        {"nav": "registry", "k": k or "", "q": _qs(k), "can_act": _can_act(k)},
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
                "can_act": _can_act(k),
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
            "can_act": _can_act(k),
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


# ------------------------------------------------------------------ the sweep
#
# The outward direction, runnable from the hosted URL.
#
# Until now a sweep needed a laptop with the Parallel key, which meant a judge
# could read our recorded evidence but could not produce their own. The key is
# in Secret Manager and this service can read it, so the button does the real
# thing: Gemini writes the phrases, Parallel searches five languages, the page
# is fetched and read, and the deterministic engine decides.
#
# `DESIGN.md` §3 said this service would deliberately hold no Parallel key. That
# has changed on purpose and it is worth being explicit about the trade: a
# public endpoint that spends partner quota is a real risk, so this is behind
# the same action key as the two uploads, it only ever sweeps the demo
# performer, and it records findings only for addresses we control.


@app.post("/sweep")
def run_sweep(request: Request, k: Optional[str] = Form(None)):
    """Run one sweep and return to the findings screen.

    Synchronous, and it takes the better part of a minute: seven live searches,
    a fetch, and a model call that reads the page. A spinner and a background
    job would be nicer and would also mean a judge cannot tell whether anything
    really happened. Waiting is the honest version.
    """
    security.check(k)
    store = get_store()

    performer_id = os.environ.get("CONSENTINEL_DEMO_PERFORMER", "perf_mira_vance")
    performer = next((p for p in store.list_performers() if p.id == performer_id), None)
    if performer is None:
        return RedirectResponse(url=f"/findings{_qs(k)}", status_code=303)

    # The planted page, on this same service. Included by address rather than
    # discovered, because a search for an invented performer finds real
    # companies and nothing about her — see the note in `consentinel/sweep.py`
    # on why those are counted and not published.
    planted = str(request.base_url).rstrip("/") + "/demo/listing"

    summary = sweep_run(
        store, performer, store.list_consents(performer.id),
        deps=HarnessDeps(
            audit=get_audit(),
            cache=get_cache(),
            armor=get_armor(),
            tracer=tracer,
            metrics=metrics,
        ),
        extra_urls=[planted] if planted.startswith("http") else [],
    )

    trace_id, span_id = tracer.current_ids()
    log.info("sweep finished", performer=performer.id, locales=summary.locales,
             batches=summary.batches, results=summary.searched,
             candidates=summary.candidates, recorded=summary.recorded,
             withheld=summary.withheld, read=summary.read,
             refused=summary.refused, verdicts=summary.verdicts,
             degraded=summary.degraded, trace_id=trace_id, span_id=span_id)
    metrics.counter("sweep.runs", outcome="degraded" if summary.degraded else "ok")

    sep = f"?k={k}&" if k else "?"
    return RedirectResponse(url=f"/findings{sep}swept={summary.recorded}"
                                f"&withheld={summary.withheld}"
                                f"&searched={summary.searched}"
                                f"&live={summary.searches - summary.searches_cached}"
                                f"&cached={summary.searches_cached}", status_code=303)


# --------------------------------------------------------------- the case file
#
# Where the outward direction ends. A verdict is not the deliverable — the
# deliverable is a file counsel can act on: what was found, the sentence it
# breaches, where the snapshot lives, and a draft notice.
#
# **There is no send button and there never will be.** `Dossier.sendable` is a
# property that returns False, so a UI asking "can I send this?" gets a straight
# no from the domain object rather than from a button nobody added yet (FR-5.5).


def _verdict_of(finding) -> "object":
    """Rebuild the verdict object from the stored row.

    The dossier builder wants a `VerdictResult` and the registry stores the
    three fields a verdict owns. Rebuilding beats storing the whole object:
    the row stays the frozen contract's shape, and anything the builder needs
    beyond those fields is a sign the finding is missing something.
    """
    from consentinel.agents.reconciler import VerdictResult

    return VerdictResult(
        verdict=finding.verdict,
        check="recorded",
        reason=finding.reasoning or "",
        matched_consent_id=finding.matched_consent_id,
        breached_consent_id=None,
        citation=finding.evidence_quote,
        territories_outside=tuple(finding.target_territories or ()),
    )


@app.post("/findings/{finding_id}/dossier")
def build_dossier(finding_id: str, k: Optional[str] = Form(None)):
    """Draft the case file for one finding. Costs a model call, so it is gated."""
    security.check(k)
    store = get_store()

    finding = next((f for f in store.list_findings() if f.id == finding_id), None)
    if finding is None:
        return RedirectResponse(url=f"/findings{_qs(k)}", status_code=303)

    performer = next((p for p in store.list_performers()
                      if p.id == finding.performer_id), None)
    consents = [c for p in store.list_performers() for c in store.list_consents(p.id)]

    bundle = build_bundle(finding, performer, _verdict_of(finding), consents=consents)
    result = DossierWriter(deps=HarnessDeps(
        audit=get_audit(), cache=get_cache(), armor=get_armor(),
        tracer=tracer, metrics=metrics,
    )).write(bundle)

    store.put_dossier(StoredDossier(
        id=f"dos_{finding.id}",
        finding_id=finding.id,
        evidence_bundle=bundle.as_dict(),
        draft_notice=result.draft or "",
    ))
    if result.ok:
        finding.status = FindingStatus.DOSSIER_DRAFTED
        store.upsert_finding(finding)

    log.info("case file drafted", finding_id=finding.id, ok=result.ok,
             reason=result.reason, grounding_failures=list(result.grounding_failures),
             repairs=result.repairs, from_cache=result.from_cache)
    metrics.counter("dossier.drafted", outcome="ok" if result.ok else "refused")

    return RedirectResponse(url=f"/findings/{finding.id}/dossier{_qs(k)}",
                            status_code=303)


@app.get("/findings/{finding_id}/dossier", response_class=HTMLResponse)
def case_file(request: Request, finding_id: str, k: Optional[str] = None):
    """The case file. Readable without the key — it is the artefact worth showing."""
    store = get_store()
    finding = next((f for f in store.list_findings() if f.id == finding_id), None)
    if finding is None:
        return RedirectResponse(url=f"/findings{_qs(k)}", status_code=303)

    stored = store.get_dossier(finding.id)
    performer = next((p for p in store.list_performers()
                      if p.id == finding.performer_id), None)
    consents = {c.id: c for p in store.list_performers()
                for c in store.list_consents(p.id)}

    return templates.TemplateResponse(
        request,
        "dossier.html",
        {
            "nav": "findings",
            "k": k or "",
            "q": _qs(k),
            "can_act": _can_act(k),
            "finding": finding,
            "performer": performer,
            "consents": consents,
            "dossier": stored,
            "bundle": (stored.evidence_bundle if stored else None),
        },
    )


# ------------------------------------------------------------- the demo pages
#
# The two WU-11 fixtures, served over real HTTP so a live sweep has something
# it can lawfully read.
#
# Why this exists rather than sweeping the open web on camera: a real sweep
# finds real websites, and `CLAUDE.md` Demo safety says we do not publish
# authorisation verdicts naming real third parties. So the sweep still makes
# real Parallel calls and still reads real pages, and the page it records a
# finding against is one we control and invented. Mira Vance and VozClone
# Studio do not exist.
#
# Nothing here is a shortcut through the pipeline: `fetch_page` fetches this
# over the network with its own guards, Triage reads it with a real model call,
# and the reconciler decides the verdict from the registry. The only thing we
# arranged is the address.

DEMO_PAGES = {
    "listing": "mira_listing_clean.html",
    "listing-injected": "mira_listing_injected.html",
}

# Files a visitor can download from the app and put straight back into it.
# Handing someone a URL and telling them to "upload a contract" is not a demo
# they can run — they would have to find a signed performer agreement first.
#
# Every one of these is ours and invented: the agreement is generated by
# `tools/make_contract_pdf.py`, and the voice and the face were generated with
# Gemini. No real person's likeness or paperwork is in here.
SAMPLE_FILES = {
    "contract.pdf": (
        "docs/mira_vance_halcyon_agreement.pdf", "application/pdf",
        "A six-page performer agreement. Clause 9 grants synthetic voice in the "
        "US and Canada; clause 10(a) withholds visual likeness. Upload it on "
        "Permission Registry."),
    "voice-clip.wav": (
        "media/NF_1042_ADR_v03.wav", "audio/wav",
        "A generated ADR line. Check it as a synthetic voice for US release and "
        "it clears; check the same file for BR and it is blocked on territory."),
    "face-still.jpg": (
        "media/mira_ref_01.jpg", "image/jpeg",
        "A generated still. Check it as a synthetic face and it is blocked — "
        "the contract withholds visual likeness."),
}


@app.get("/try", response_class=HTMLResponse)
def try_it(request: Request, k: Optional[str] = None):
    """The walkthrough. Four steps, in order, with the files to do them with.

    A hosted URL and a login-free console still leave a visitor guessing what to
    click first, and the two most interesting paths — reading a contract and
    checking a clip — need a file they do not have.
    """
    return templates.TemplateResponse(
        request,
        "try.html",
        {
            "nav": "try",
            "k": k or "",
            "q": _qs(k),
            "can_act": _can_act(k),
            "samples": SAMPLE_FILES,
        },
    )


@app.get("/demo/samples/{name}")
def sample_file(name: str):
    """Hand over one sample file."""
    from fastapi.responses import FileResponse

    entry = SAMPLE_FILES.get(name)
    if entry is None:
        return HTMLResponse("no such sample", status_code=404)
    relative, media_type, _ = entry
    path = HERE.parent / "fixtures" / relative
    if not path.exists():
        return HTMLResponse("sample missing from this build", status_code=404)
    return FileResponse(path, media_type=media_type, filename=name)


@app.get("/demo/{name}", response_class=HTMLResponse)
def demo_page(name: str):
    """One of the planted fixtures, byte for byte.

    `listing-injected` is the same page with one paragraph added that addresses
    our agent directly. Serving it is the point: the injection defence is worth
    more demonstrated against a live fetch than asserted in a test.
    """
    filename = DEMO_PAGES.get(name)
    if filename is None:
        return HTMLResponse("no such demo page", status_code=404)

    path = HERE.parent / "fixtures" / "pages" / filename
    if not path.exists():
        return HTMLResponse("demo fixture missing from this build", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# ------------------------------------------------------ checking our own clip
#
# The other direction, and the same gate: reading a clip costs a Gemini call.
#
# Everything the rule engine needs about our own footage is on the delivery
# note, so the form asks for it rather than making a model guess at facts the
# submitter already knows. What the model is for is the one thing the note
# cannot establish: whether the file actually contains what the note says.


@app.post("/clearance/check")
async def clearance_check(
    clip: UploadFile = File(...),
    k: Optional[str] = Form(None),
    performer_id: str = Form(...),
    licensee: str = Form(...),
    modality: str = Form(...),
    territories: str = Form(""),
    vendor: str = Form(""),
    invoice_ref: str = Form(""),
    shot_code: str = Form(""),
    synthetic: str = Form("unknown"),
):
    """Check one clip and write the answer. Never raises on a bad file — every
    failure lands as `unverified`, because a crash read as a pass is the one
    outcome this screen must not produce."""
    security.check(k)
    store = get_store()

    data = await clip.read()
    filename = Path(clip.filename or "clip").name
    production = os.environ.get("CONSENTINEL_DEMO_PRODUCTION", "prod_halcyon_nightfall")

    declaration = Declaration(
        performer_id=performer_id,
        licensee=licensee.strip(),
        modality=modality.strip().lower(),
        territories=tuple(t.strip().upper() for t in territories.split(",") if t.strip()),
        vendor=vendor.strip() or None,
        invoice_ref=invoice_ref.strip() or None,
        shot_code=shot_code.strip() or None,
        production_id=production,
        synthetic=synthetic if synthetic in ("yes", "no", "unknown") else "unknown",
    )

    consents = [c for p in store.list_performers() for c in store.list_consents(p.id)]

    outcome = check_asset(
        data=data,
        filename=filename,
        mime_type=clip.content_type or "",
        declaration=declaration,
        consents=consents,
        deps=HarnessDeps(
            audit=get_audit(),
            cache=get_cache(),
            armor=get_armor(),
            tracer=tracer,
            metrics=metrics,
            prompt_version=CLEARANCE_PROMPT_VERSION,
        ),
    )

    # Keyed on the file's bytes *and* the shot it was submitted as. The bytes
    # alone are not enough: the same master can be delivered for two markets,
    # and hashing only the file would let the second delivery quietly overwrite
    # the first one's answer. Keeping the shot in the key also gives us the
    # thing the demo needs — re-check the same delivery after adding a
    # permission slip and the row updates rather than doubling.
    row_key = hashlib.sha256(
        f"{outcome.content_hash}:{declaration.shot_code or filename}".encode()
    ).hexdigest()[:12]
    asset = to_asset(outcome, declaration, asset_id=f"asset_{row_key}", filename=filename)
    store.upsert_asset(asset)

    trace_id, span_id = tracer.current_ids()
    log.info("checked a clip",
             filename=filename, state=enum_value(outcome.state), check=outcome.check,
             declared=outcome.declared_modality, perceived=outcome.perceived_modality,
             matched_consent_id=outcome.matched_consent_id,
             from_cache=outcome.read_from_cache,
             armor=describe_armor(get_armor()),
             trace_id=trace_id, span_id=span_id)
    metrics.counter("clearance.checked", state=enum_value(outcome.state) or "unknown")

    sep = f"?k={k}&" if k else "?"
    return RedirectResponse(url=f"/clearance{sep}checked={asset.id}", status_code=303)
