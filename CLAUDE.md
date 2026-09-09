# Consentinel — read this first

This file is the hub. Three people are building this in parallel with separate Claude Code sessions
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
Cloud Vision web detection · **Firestore** behind `Store` · FastAPI + Jinja · Agent Runtime + Cloud Run.

## Canonical external references — check these, don't trust our summaries

Our docs summarise these pages, and **the organisers can update them at any time**. When a question
touches rules, eligibility, submission requirements or which services are sanctioned, fetch the live
page rather than relying on `COMPETITION.md`.

| Source | URL | Ours that summarises it |
|---|---|---|
| **Rules** | https://agentic-cinema.devpost.com/rules | [COMPETITION.md](COMPETITION.md) |
| **Resources** | https://agentic-cinema.devpost.com/resources | [COMPETITION.md](COMPETITION.md) §12 |
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

## Branching and pull requests

**One branch and one PR per epic.** Not per work unit - a PR per WU would mean forty reviews in two
days, and nobody would read any of them.

```
epic/0-harness                 WU-00                     merged to main
epic/1-consent-registry        WU-01, WU-02, WU-03
epic/2-web-discovery           WU-04..WU-07, WU-26..WU-28, WU-32
epic/3-safe-page-reading       WU-08..WU-11, WU-29
epic/4-verdicts                WU-12, WU-13, WU-14
epic/5-enforcement-output      WU-15, WU-16
epic/6-delivery-clearance      WU-17
epic/7-auditability            WU-18
epic/8-performance-resilience  WU-19, WU-20, WU-30, WU-33
epic/9-deployment              WU-21..WU-25, WU-34
epic/10-submission             WU-35, WU-36, WU-37
epic/11-evaluation             WU-31
```

**Rules**
- Branch from up-to-date `main`. `git pull` first, every time
- Commit per work unit, so a PR reads as a sequence of finished units rather than one blob
- **Push early**, before the epic is finished. An open PR is how the other person sees that a file
  is being worked on, which is the actual defence against merge conflicts
- Tests pass before you push. `python -m pytest tests/ -q`
- Squash-merge to `main`, then delete the branch
- **Do not review your own PR into main if the other engineer is awake.** If they are asleep and it
  is P0, merge it and say so in the PR body

**A PR body should say:** which work units it closes, which stories it delivers, the test count, and
anything the reviewer should push back on. Not a restatement of the diff - the diff is right there.

**Frozen-contract changes never travel in an epic PR.** `schema.sql`, `store/base.py` and
`tools/contracts.py` get their own small PR so the change is visible rather than buried in four
hundred lines of feature work.

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
- Agent Runtime deployment

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

## Agent layout — follow this for every agent

One folder per agent, three files, plus anything genuinely shared in
`agents/common/`:

```
consentinel/agents/common/gemini.py        the only place we call a model
consentinel/agents/<agent>/
    __init__.py       re-exports; import from the package, not the modules
    agent.py          the steps, and the HarnessPolicy
    guardrail.py      what can reject an answer
    instructions.py   the prompt, the response schema, PROMPT_VERSION
```

Why split rather than one file:

- **Prompts change constantly.** Keeping them alone means a wording tweak is a
  one-file diff, and `PROMPT_VERSION` sits next to the words it versions — bump
  it when they change, or the cache serves answers from a prompt you deleted.
- **`guardrail.py` is the security surface.** Someone asking "what stops it
  inventing a citation?" should find one short file, not a function three
  hundred lines into the agent.
- Every model call goes through `agents/common/gemini.py`, so the
  untrusted-content boundary is drawn once instead of six times.

`consent_ingest` is the worked example. Copy its shape.

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
- Prefer a resource listed in [COMPETITION.md](COMPETITION.md) §12 over rolling your own
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

Reference images and the fake cloned-voice clip are **generated by us** with Gemini image
generation and Gemini TTS on Vertex — `tools/make_demo_media.py`, so anyone can re-run it. **Imagen
is not available on project `consentinel`**: every `imagen-*` id 404s in `us-central1` and `global`,
and none appear in `models.list()`. The requirement was never the model, it was that the face and
the voice are invented rather than borrowed
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
- [x] WU-01 Firestore store, WU-02 seed loader — *Prachit* — real registry seeded
- [x] WU-18 audit helper, WU-19 cache (`consentinel/cache/`), WU-30 observability — *Prachit*
- [x] `parallel_search` via official `parallel-web` SDK — *Vedant* (WU-05, 23 tests)
- [x] `QueryPlanner` — *Vedant* (WU-06, 31 tests; 5 locales, 5 languages, deterministic fallback)
- [x] `TextSweep` — *Vedant* (WU-07, 24 tests; dedupe on `url_hash`, 25-candidate cap)
- [x] `fetch_page` + `web_risk_check` — *Vedant* (WU-08, 60 tests; SSRF guards, fails closed)
- [x] `Triage` + extraction validators — *Vedant* (WU-09/WU-10, 67 tests; no tools, quote verbatim)
- [x] Injection canary + the planted demo page — *Vedant* (WU-11, 43 tests)
- [x] **Model Armor, implemented and connected** — *Vedant* (WU-29 armor half, 34 tests).
      `consentinel/model_armor.py` is the real screen; the three templates exist on the project and
      `web/app.py` passes `armor=get_armor()` into `HarnessDeps`. **Import the template names from
      `model_armor` (`TRIAGE_IN`/`INGEST_IN`/`NOTICE_OUT`) — never retype them.** A wrong name does
      not raise: an inbound screen resolves `armor_unavailable` and carries on, so the feature goes
      dark with one log line as the only sign. That is exactly what had happened to `consent_ingest`
- [x] `Reconciler` (deterministic rules, unit-tested) — *Vedant* (WU-12/WU-13, 50 tests; no model)
- [x] Reliability policy + visibly-degraded sweeps — *Vedant* (WU-14, 39 tests)
- [x] `DEMO_MODE` across every client + warm-cache script — *Vedant* (WU-20, 9 tests)
- [x] `adversarial_injection` + `verdict_matrix` evalsets, running in CI — *Vedant* (WU-31)
- [x] Findings UI + decision-trail view — *Prachit* (WU-21/WU-22/WU-23, the three screens)
- [x] **The console UI, matching the agreed design** — *Prachit* (`ui/console-from-mockup`).
      One stylesheet, `web/static/console.css`, taken from the mockup the Figma board was built
      from. `style.css` and `theme.css` are gone — do not add a second stylesheet. There is now a
      front page at `/` and the registry moved to `/registry`; rows open a detail panel, which
      `web/static/console.js` fills **by cloning server-rendered nodes, never from a string**,
      because findings carry text from pages we do not control
- [x] `ConsentIngest` (contract PDF → permission grant) — *Prachit* (WU-03)
- [x] `DossierWriter` + snapshot capture — *Vedant* (WU-16 + WU-15's capture half, 35 tests;
      grounded drafts, no send path anywhere). **`consentinel/evidence/store.py` is still
      Prachit's half of WU-15** — a stopgap `LocalSnapshotStore` lives in `agents/snapshot.py`
      until it lands
- [x] Cloud Run deploy + IAM — *Prachit* (WU-25, WU-34: six service accounts, three buckets)
- [x] **`ClearancePipeline` — *Prachit* (WU-17).** `consentinel/agents/clearance/`, live in the app
      at `POST /clearance/check`. It calls **the same reconciler as the sweep** — do not write a
      second rule engine for the inward direction, that equivalence is the product's claim. Gemini
      reads the file and can only ever *withhold* clearance (by disagreeing with the delivery note
      or finding no person); clearance itself comes from a contract record the model never sees.
      Verified live: voice/US cleared, voice/BR blocked, face/US blocked. **Not built:** Content
      Credentials parsing, invoice/SOW parsing, proxy transcode, manifest rollup — the form takes
      declared facts instead
- [ ] `consentinel/evidence/store.py` — *Prachit* (WU-15's store half; my capture half is done)
- [ ] **WU-24 four Agent Runtime deployments** — *Vedant* — **P0, needs GCP credentials**
- [x] **WU-37 runtime evidence pack** — *Vedant* — `evidence/published/` is in the repo: 8 live
      Parallel calls with `cache=miss` and their real `search_id`s, plus 8 `cache=hit` proving
      DEMO_MODE, generated by `tools/redact_evidence.py`. `evidence/runtime/` holds the unredacted
      original and **stays gitignored** — it names seventeen real hosts
- [ ] **WU-36 demo video, Devpost writeup** — *Swara* — **P0**
- [ ] `ImageSweep` (WU-32), AudioSweep/VideoSweep (WU-26/27), `fetch_media` (WU-28),
      MediaTriage (WU-29), scheduled sweeps (WU-33) — *Vedant* — all P1/P2, all in the cut order

**The judged integration has run.** The Parallel key is in `.env`, a live `--fresh` sweep made 8
real `client.search(...)` calls across 5 locales, and the log is committed at
`evidence/published/`. That closes the gap `COMPETITION.md` §11 calls fatal.

**Now blocking the submission, in this order:**

1. **The video is not shot.** *Swara.* Longest pole.
2. **The Devpost form**, with the Parallel track selected.

**Both upload paths work on the hosted URL**, which is what a demo needs: a contract PDF at
`/consents/new` and a clip at `/clearance`. Both spend a Gemini call, so both need `?k=<token>`
from Secret Manager (`consentinel-action-token`) — reads stay open. Without the key the forms are
hidden rather than shown and refused.

**The hosted URL is recorded and live:** https://consentinel-web-255860737849.us-central1.run.app —
it is in the README's first screenful and in `COMPETITION.md` §11. Cloud Run, `us-central1`, public,
no login. Revision `00008`, the console UI, all four screens tested from a cold browser. Cloud Run
also answers on `https://consentinel-web-ayr2oo7nzq-uc.a.run.app` (what `describe` reports as
`status.url`); both work, and the submission uses the project-number form above.

**Cut:** WU-24, the four Agent Runtime deployments. It has no scaffolding at all, and
`COMPETITION.md` §5 is explicit that Cloud Run satisfies the platform requirement — Agent Engine is
a Technological-Implementation bonus, not a submission item. Not worth starting with the hosted URL
still unverified.

## Open questions

| ID | Question | Owner |
|---|---|---|
| OQ-3 | Current Cloud Vision web-detection API surface and quota | Vedant |
| OQ-4 | Screenshot capture approach for evidence snapshots on Cloud Run | Prachit |
| OQ-6 | **Does `Finding` get an `injection_suspected` boolean?** WU-11 needs one and the frozen contract has none, so the flag currently rides in `reasoning` behind `[injection_suspected: markers]` (`agents/injection_canary.py`). Adding the field is one line in `store/base.py` + `schema.sql` and a MAJOR bump — needs all three to agree. **Answer this before WU-22 builds the badge**, or the UI codes against the workaround | all three |

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

**OQ-2 — closed by decision.** We are not adopting a second partner product for v1. Firestore behind
the `Store` interface; ClickHouse remains a documented post-hackathon path.

**Contract consequence:** `search_queries` takes **2–3 keyword queries of 3–6 words each** per call,
alongside a natural-language `objective`. Other useful parameters: `mode`
(turbo|fast|basic|advanced — we default to `basic`), `max_results`, `max_chars_total`,
`source_policy.exclude_domains`, `source_policy.after_date`, `session_id`. Response is
`search_id`, `results[]` (`url`, `title`, `publish_date`, `excerpts[]`), `warnings`, `session_id`.

### Verified against the installed SDK — 9 Sep 2026, `parallel-web` 1.3.3 (WU-05)

Two details the summary above does not capture. Build WU-06 and WU-07 against these, not against
the doc prose:

- The call is **`client.search(...)`** — top level, not `client.beta.search`.
- **`location`, `max_results` and `source_policy` are not top-level arguments.** They live inside
  `advanced_settings`. Only `search_queries`, `objective`, `mode`, `max_chars_total`, `session_id`
  and `client_model` are top level. `consentinel/tools/parallel_search.py` does this mapping;
  a test asserts every key we send exists in the installed SDK's param types, so drift fails loudly.
- There is no language parameter — the query text carries the language, as WU-04 found.
