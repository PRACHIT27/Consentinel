# Consentinel — read this first

This file is the hub. Two people are building this in parallel with separate Claude Code sessions
that cannot see each other, so everything either of us needs to stay consistent lives here or is
linked from here.

## What we're building

> **Consentinel finds people using an actor's face or voice without consent, and stops a studio
> from accidentally shipping something it never had the rights to.**

A consent registry (the spine), plus two agent pipelines reading from it in opposite directions:
**enforcement** (sweep the web for unauthorised use → evidence + draft notice) and **clearance**
(check our own deliverables → cleared / blocked / unverified). Both share one rule engine.

Agentic Cinema hackathon, **Parallel track**. Deadline **9 Sep 2026, 2:00 PM PDT** — we submit by
**12:00 PDT**.

Stack: Python 3.11+ · Google ADK · Gemini on Vertex · Parallel Search (`parallel-web` SDK) ·
Cloud Vision web detection · SQLite behind `Store` · FastAPI + Jinja · Agent Engine + Cloud Run.

---

## Document map — read the one that matches your task

| Doc | What's in it | Read it when |
|---|---|---|
| [TASKS.md](TASKS.md) | Ticketed backlog T-01…T-49 with owners, priorities, estimates, dependencies, acceptance. Critical path and day plan | **Start here every session** — pick your next ticket. Canonical over Notion |
| [PRD.md](PRD.md) | Numbered requirements FR-1…FR-8, TS-1…TS-6, acceptance criteria, personas, non-goals, milestones, open questions | Before building any feature — find your FR number and its acceptance criterion |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Seven Mermaid diagrams: system context, components, both pipeline sequences, trust boundary, ER model, verdict rule flow | When you need to see how a piece fits, or to paste a diagram into Notion or the writeup |
| [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) | Scheduler, retry policy, model guardrails, prompt-injection defence, storage zones, cache regimes | Before writing a tool, a model call, or anything that touches storage or caching |
| [COMPETITION.md](COMPETITION.md) | Verbatim rules, required SDKs, runtime-evidence requirement, submission checklist, judging criteria, disqualification risks | Before adding a dependency, choosing a deploy target, or preparing the submission |
| [RESOURCE_MAP.md](RESOURCE_MAP.md) | Every hackathon resource mapped to the component that uses it | When picking how to implement something — prefer a listed resource; it's 25% of the score |
| [BUILD_SPEC.md](BUILD_SPEC.md) | The five features in plain language: user action → system steps → screen → done-when | When you want the simple version of what a feature does |
| [TEAM_BRIEF.md](TEAM_BRIEF.md) | Onboarding for a new teammate; plain-language problem framing | First read, or when explaining the project to someone |
| [README.md](README.md) | Public-facing pitch, architecture diagram, setup, scope boundary | When editing anything a judge will read |

**Frozen contract (code):** [schema.sql](schema.sql) ·
[consentinel/store/base.py](consentinel/store/base.py) ·
[consentinel/tools/contracts.py](consentinel/tools/contracts.py)

---

## Hard rules — do not violate

These are load-bearing. Breaking one breaks the product's core claim. Rationale in
[TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md).

1. **Fetched web content is data, never instructions.** Delimited field only; never composed into a
   system prompt. Pages may contain text addressed to the agent.
2. **The reconciler never sees raw page text** — validated structured fields plus registry rows only.
3. **Extraction agents get no tools and no free-form output channel.** Schema-constrained only.
4. **The model never decides a verdict.** Verdicts are deterministic rule evaluation (FR-4.2).
5. **`evidence_quote` must be a verbatim substring of the source text.** Reject the extraction
   otherwise. One string comparison; makes quote hallucination structurally impossible.
6. **No outward action is ever automatic.** Notices are drafted; humans send them. No send capability
   may exist in the product.
7. **Never cache verdicts.** They derive from a mutable registry. Recompute on read.
8. **Cache keys include `prompt_version`** (model calls) **and `locale`** (anything web-facing).
9. **Evidence snapshots are immutable, no TTL.** They are not the cache.
10. **Fail toward doubt, never toward permission.** Errors resolve to `ambiguous` / `unverified` /
    `degraded` — never to `authorized` or `cleared`. A crash must be distinguishable from a clean
    result.
11. **Assets default to `unverified`.** We prove coverage; we do not detect AI.
12. **Gemini only.** No OpenAI/Anthropic/AWS/Microsoft model or API in the shipped pipeline — that
    is a competition rule (`COMPETITION.md` §3.1). Using Claude Code to *write* the code is fine.

---

## Frozen contract

`schema.sql`, `consentinel/store/base.py` and `consentinel/tools/contracts.py` are agreed
boundaries. **Do not change a field, enum or signature alone** — the other half of the build is
coded against it. If a change is genuinely needed, say so in chat and get agreement first; don't
edit silently.

`fixtures/seed.json` ships fake data in the real shapes, so the UI can be built before the pipeline
exists and the pipeline tested before the UI does. Nobody blocks anybody.

---

## File ownership

Stay in your lane. Do not refactor across this boundary — merge conflicts are the main risk when two
people each have an AI writing code fast. (Swapping is fine; edit this section if you do.)

**Vedant — agent side**
- `consentinel/agents/query_planner.py`
- `consentinel/agents/triage.py`
- `consentinel/agents/reconciler.py`
- `consentinel/agents/dossier_writer.py`
- `consentinel/tools/parallel_search.py`
- `consentinel/tools/fetch_page.py`
- `consentinel/tools/vision_web_detection.py`
- Agent Engine deployment

**Prachit — foundation and app**
- `consentinel/store/` implementations
- `consentinel/cache/`
- `consentinel/evidence/`
- `consentinel/agents/consent_ingest.py`
- `consentinel/agents/clearance/`
- `web/`
- `fixtures/`, schema migrations
- Cloud Run deployment

**Swara — PM, docs and demo**
- `TASKS.md` (owner of the backlog; mirrors it into Notion)
- Notion workspace: task board, user stories, architecture diagrams
- The **Status** section of this file — she syncs it from Notion twice daily
- Demo fixtures: generated reference images and cloned-voice clip, the fictional contract PDF
- Demo video script, recording, Devpost writeup, final submission checklist

**Shared — edit with care, pull first:** `README.md`, `CLAUDE.md`, `requirements.txt`, the docs.

If you are doing PM work, read [TASKS.md](TASKS.md) first — it holds the ticket breakdown, the
critical path, the day plan and the cut order. Notion mirrors it; this repo is canonical, because
Claude Code sessions read files and cannot see Notion.

---

## Conventions

- Type hints everywhere; dataclasses for records
- Compose pipelines with ADK `SequentialAgent` / `ParallelAgent`, not LLM routing — deterministic
  composition demos far more reliably
- Temperature 0 for every extraction call. The only place sampling is acceptable is the draft notice
- Use **forced function calling** to guarantee structured extraction output
- Every agent step and tool call appends to `audit_log`, including `from_cache` and `cache_age_s`
- Metadata in the database, bytes in object storage. Never blobs in SQL
- Secrets from `.env` locally, Secret Manager in deploy. **The repo is public — never commit a key.**
  If one lands in history, rotate it; deleting it in a later commit does not remove it
- Prefer a resource listed in [RESOURCE_MAP.md](RESOURCE_MAP.md) over rolling your own
- Verify Google API surfaces (ADK, Gemini, Cloud Vision) against current docs before building on
  them — some names in `contracts.py` were written from memory and may have moved

---

## Session start ritual

1. `git pull`
2. Read the **Status** section below — don't rebuild finished work
3. Check **Open questions** — if one blocks you, answer it first or say so
4. Find your FR number in [PRD.md](PRD.md) and its acceptance criterion
5. Build, commit small, push
6. **Update Status before you finish**

---

## Demo safety

`fixtures/seed.json` uses a fictional performer (*Mira Vance*) and licensee on purpose. The repo and
video are public, so we do not publish authorisation verdicts about real people or name real
third-party sites on camera. Sweep a category; redact identifiers in the recording.

Reference images and the fake cloned-voice clip are **generated by us** with Imagen 3 and Gemini TTS
— so no real person's likeness appears anywhere in the submission. See `RESOURCE_MAP.md`.

`DEMO_MODE=true` must keep working: the full pipeline runs from cache with zero external calls, so
the video shoot cannot die on a rate limit at 1am.

---

## Status

Ticket-level detail lives in [TASKS.md](TASKS.md); Swara keeps this summary in sync from Notion
twice daily. **Read it before you start, update it before you finish.**

- [x] Frozen contract: schema, `Store`/`Cache`/`EvidenceStore` interfaces, tool signatures, fixtures
- [x] Docs: PRD, technical design, competition requirements, resource map, build spec, team brief,
      architecture diagrams, backlog
- [x] Google Cloud hackathon credits obtained
- [ ] Store implementation (SQLite) + seed loader — *Prachit*
- [ ] Cache layer, both regimes — *Prachit*
- [ ] `parallel_search` via official `parallel-web` SDK + `TextSweep` — *Vedant*
- [ ] `Triage` + `Reconciler` (deterministic rules, unit-tested) — *Vedant*
- [ ] Findings UI + decision-trail view — *Prachit*
- [ ] `ConsentIngest` (contract PDF → permission grant) — *Prachit*
- [ ] `DossierWriter` + evidence snapshots — *Vedant*
- [ ] `ClearancePipeline`, slim — *Prachit*
- [ ] Agent Engine deploy — *Vedant*
- [ ] Cloud Run deploy, cold-start tested — *Prachit*
- [ ] `ImageSweep` (only if ahead of schedule) — *Vedant*
- [ ] Demo video, Devpost writeup, runtime-evidence screenshots — *both*

## Open questions

| ID | Question | Owner |
|---|---|---|
| OQ-1 | Which region/language parameters does Parallel's Search API expose? Affects the query planner and every web cache key | Vedant |
| OQ-5 | Does Parallel's **Extract API** return full page content? If yes, use it instead of our own `fetch_page` — partner service then covers discovery *and* retrieval | Vedant |
| OQ-2 | Do the rules permit using a second partner's product (e.g. ClickHouse) alongside our track? | either |
| OQ-3 | Current Cloud Vision web-detection API surface and quota | Vedant |
| OQ-4 | Screenshot capture approach for evidence snapshots on Cloud Run | Prachit |

OQ-1 and OQ-5 are ticket **T-07** and block the entire agent side. Do them first.
Swara tracks all four to closure (T-49).
