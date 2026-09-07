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

## Canonical external references — check these, don't trust our summaries

Our docs summarise these pages, and **the organisers can update them at any time**. When a question
touches rules, eligibility, submission requirements or which services are sanctioned, fetch the live
page rather than relying on `COMPETITION.md` or `COMPETITION.md §12`.

| Source | URL | Ours that summarises it |
|---|---|---|
| **Rules** | https://agentic-cinema.devpost.com/rules | [COMPETITION.md](COMPETITION.md) |
| **Resources** | https://agentic-cinema.devpost.com/resources | [COMPETITION.md §12](COMPETITION.md §12) |
| Overview | https://agentic-cinema.devpost.com/ | [COMPETITION.md](COMPETITION.md) §2 |
| Forum | https://agentic-cinema.devpost.com/forum_topics | — |
| Discord | https://discord.gg/7Dqk5ebCD4 | — |

If a live page contradicts one of our docs, **the live page wins**. Fix our doc, and log it in
[VERSION.md](VERSION.md).

Also verify Google API surfaces against current Google docs (ADK, Gemini, Cloud Vision) and
Parallel's own reference — some names in `contracts.py` were written from memory.

---

## Document map — read the one that matches your task

Eight files, deliberately. If you want to add a ninth, extend one of these instead.

| Doc | What's in it | Read it when |
|---|---|---|
| [BUILD_PROMPTS.md](BUILD_PROMPTS.md) | A ready-to-paste prompt per work unit, plus the ticket index and cut order | **Start here every session.** Paste your work unit's prompt instead of improvising context |
| [VERSION.md](VERSION.md) | Change log for the docs and frozen contract; doc-set version; architecture freeze status | **Second thing you read** — what moved since last session, and any *Action required* |
| [PRD.md](PRD.md) | Requirements FR-1…FR-8, TS-1…TS-6, acceptance criteria, personas, non-goals | Before building a feature — find its requirement and acceptance criterion |
| [DESIGN.md](DESIGN.md) | **Part I** fail-safe rule, retries, guardrails, injection defence, storage, cache. **Part II** Firestore, four runtimes, IAM, the agent harness, Model Armor, observability, evaluation | Before writing a tool, a model call, an agent, or anything touching storage, IAM or deployment |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Nine Mermaid diagrams: context, components, both sequences, trust boundary, data model, verdict rules, deployment, harness | When you need to see how a piece fits, or to paste a diagram into Notion |
| [COMPETITION.md](COMPETITION.md) | Verbatim rules, required SDKs, runtime-evidence requirement, submission checklist, judging criteria, and §12 the hackathon resources mapped to components | Before adding a dependency, choosing a deploy target, or preparing the submission |
| [README.md](README.md) | Public pitch, how it works in plain language, what we do not claim, setup | When editing anything a judge will read |

**Frozen contract (code):** [schema.sql](schema.sql) ·
[consentinel/store/base.py](consentinel/store/base.py) ·
[consentinel/tools/contracts.py](consentinel/tools/contracts.py)

---

## Hard rules — do not violate

These are load-bearing. Breaking one breaks the product's core claim. Rationale in
[DESIGN.md](DESIGN.md).

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

## Versioning — every doc change gets logged

Changing a **governed file** (any top-level `.md`, `schema.sql`, `store/base.py`,
`tools/contracts.py`) requires a [VERSION.md](VERSION.md) entry **in the same commit**. A
`pre-commit` hook enforces this.

**Run once after cloning:**
```bash
git config core.hooksPath .githooks
```

Write the entry for whoever reads it next, not for the record. "Renamed a field" is useless;
"renamed `consents.territory` → `territories`, now a list — update any query against it" is what a
teammate needs. MAJOR = frozen-contract or post-freeze architecture change · MINOR = new doc,
requirement or scope · PATCH = clarification that changes nothing being built against.

Ordinary implementation files are not governed — commit those freely.

## Architecture freeze

`ARCHITECTURE.md` is **🔒 FROZEN at v1.0.0** (7 Sep 2026). Safe to design against. A structural
change from here needs all three of us to agree and a MAJOR bump in [VERSION.md](VERSION.md).

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
- `BUILD_PROMPTS.md` (owner of the backlog; mirrors it into Notion)
- Notion workspace: task board, user stories, architecture diagrams
- The **Status** section of this file — she syncs it from Notion twice daily
- Demo fixtures: generated reference images and cloned-voice clip, the fictional contract PDF
- Demo video script, recording, Devpost writeup, final submission checklist

**Shared — edit with care, pull first:** `README.md`, `CLAUDE.md`, `requirements.txt`, the docs.

If you are doing PM work, read [BUILD_PROMPTS.md](BUILD_PROMPTS.md) first — it holds the ticket breakdown, the
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
- Prefer a resource listed in [COMPETITION.md §12](COMPETITION.md §12) over rolling your own
- Verify Google API surfaces (ADK, Gemini, Cloud Vision) against current docs before building on
  them — some names in `contracts.py` were written from memory and may have moved

---

## Session start ritual

1. `git pull`
2. Read the newest entries in [VERSION.md](VERSION.md) — what moved since you last looked, and
   whether anything says **Action required**
3. Pick your next ticket from [BUILD_PROMPTS.md](BUILD_PROMPTS.md); check the **Status** section below so you don't
   rebuild finished work
4. Check **Open questions** — if one blocks you, answer it first or say so
5. Find the ticket's requirement in [PRD.md](PRD.md) and read its acceptance criterion
6. Build, commit small, push. Governed file? Add a `VERSION.md` entry in the same commit
7. **Update Status before you finish**

First time on this machine: `git config core.hooksPath .githooks`

**WU-00, the agent harness, blocks every other work unit.** Whoever starts first builds it.

---

## Demo safety

`fixtures/seed.json` uses a fictional performer (*Mira Vance*) and licensee on purpose. The repo and
video are public, so we do not publish authorisation verdicts about real people or name real
third-party sites on camera. Sweep a category; redact identifiers in the recording.

Reference images and the fake cloned-voice clip are **generated by us** with Imagen 3 and Gemini TTS
— so no real person's likeness appears anywhere in the submission. See `COMPETITION.md §12`.

`DEMO_MODE=true` must keep working: the full pipeline runs from cache with zero external calls, so
the video shoot cannot die on a rate limit at 1am.

---

## Status

Ticket-level detail lives in [BUILD_PROMPTS.md](BUILD_PROMPTS.md); Swara keeps this summary in sync from Notion
twice daily. **Read it before you start, update it before you finish.**

- [x] Frozen contract: schema, `Store`/`Cache`/`EvidenceStore` interfaces, tool signatures, fixtures
- [x] Docs: PRD, technical design, competition requirements, resource map, build spec, team brief,
      architecture diagrams, backlog
- [x] Google Cloud hackathon credits obtained
- [ ] Store implementation (SQLite) + seed loader — *Prachit*
- [ ] WU-02 seed loader — *Prachit*
- [ ] WU-18 audit helper, WU-19 cache — *Prachit* (plug into the harness ports)
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
| OQ-3 | Current Cloud Vision web-detection API surface and quota | Vedant |
| OQ-4 | Screenshot capture approach for evidence snapshots on Cloud Run | Prachit |

Both remaining questions affect P2/P1 work only and gate nothing. Swara tracks them (T-49).

### Answered — 7 Sep 2026, from docs.parallel.ai

**OQ-1 — locale: fully supported.** `location` takes an **ISO 3166-1 alpha-2 country code**
(lowercase, e.g. `br`), inside advanced settings. Queries can be written in **any language** with no
extra configuration — 26+ languages, 30+ countries. `Locale.region` → `location`;
`Locale.language` → the language the query text is written in.

**OQ-5 — Extract does NOT return full page content.** It returns compressed, objective-scoped
excerpts. It therefore **cannot replace `fetch_page`** and **cannot serve as evidence**.
`fetch_page` stays. Extract is adopted as an optional **P1** cheap first-pass read during triage,
with `fetch_page` reserved for candidates escalating to a dossier.

**OQ-2 — closed by decision.** We are not adopting a second partner product for v1. SQLite behind
the `Store` interface; ClickHouse remains a documented post-hackathon path.

**Contract consequence:** `search_queries` takes **2–3 keyword queries of 3–6 words each** per call,
alongside a natural-language `objective`. Other useful parameters: `mode`
(turbo|fast|basic|advanced — we default to `basic`), `max_results`, `max_chars_total`,
`source_policy.exclude_domains`, `source_policy.after_date`, `session_id`. Response is
`search_id`, `results[]` (`url`, `title`, `publish_date`, `excerpts[]`), `warnings`, `session_id`.
