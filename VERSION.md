# Version log

Change record for the **governed files** — the docs and the frozen contract. Every change to one of
them gets an entry here, in the same commit. A `pre-commit` hook enforces it (see
[Enforcement](#enforcement)).

Why: three people are working with separate Claude Code sessions that cannot see each other. This
file is how a session finds out that the contract moved since it last looked.

**Doc set version: `v1.3.1`**
**Architecture status: 🔒 FROZEN** (7 Sep 2026) — safe to design against. Structural changes from
here need all three to agree and a MAJOR bump.

---

## Governed files

Changes to any of these require a VERSION.md entry:

| File | Why it's governed |
|---|---|
| `schema.sql` | Frozen contract — the logical model both halves code against |
| `consentinel/store/base.py` | Frozen contract — interfaces and records |
| `consentinel/tools/contracts.py` | Frozen contract — tool signatures |
| `PRD.md` | Requirements; changes invalidate acceptance criteria |
| `DESIGN.md` | Design decisions others are implementing against |
| `ARCHITECTURE.md` | Swara designs from this; frozen at v1.0.0 |
| `COMPETITION.md` | Rules, submission requirements, resource mapping |
| `BUILD_PROMPTS.md` | Per-work-unit prompts; changing one changes what gets built |
| `CLAUDE.md` | The hub every session loads |
| `README.md` | Public face; judges read it |

Ordinary implementation files are **not** governed — commit them freely without touching this file.

## Versioning scheme

`MAJOR.MINOR.PATCH` for the document set as a whole.

- **MAJOR** — a frozen-contract change (schema, interfaces, tool signatures), or an architecture
  change after freeze. Requires all three of us to agree
- **MINOR** — a new document, a new requirement, a new epic, a scope change
- **PATCH** — clarifications, typos, added detail that changes nothing anyone is building against

## Entry format

```
## vX.Y.Z — YYYY-MM-DD — Author
**Files:** the governed files touched
**Type:** MAJOR | MINOR | PATCH
- what changed, and why it matters to someone else
- **Action required:** anything a teammate must do because of this (omit if none)
```

Write the entry for the *reader*, not the record. "Renamed a field" is useless; "renamed
`consents.territory` → `territories` (now a list) — update any query you wrote against it" is what
someone needs.

---

## Architecture freeze

Swara cannot design Notion diagrams against a moving target, so `ARCHITECTURE.md` gets frozen once
the open questions that could change it are answered.

**Blocking:** ticket **T-07** — Parallel's API reference.

- **OQ-5** — if the Extract API returns full page content, it *replaces* our own `fetch_page`.
  That changes diagrams 1, 2, 3 and 5
- **OQ-2** — if a second partner product is permitted and we adopt ClickHouse, that adds a
  component to diagrams 1 and 2

**OQ-1** (locale parameters) affects the query planner's behaviour but not the structure, so it does
not block the freeze.

**Freeze procedure**
1. Vedant answers T-07 and records the answers in `CLAUDE.md`
2. Apply any resulting changes to `ARCHITECTURE.md`
3. All three confirm — in the Notion page or the group chat
4. Bump to **v1.0.0** here, set status to 🔒 FROZEN, and log the sign-off
5. Swara designs from that version. After freeze, a structural change is a MAJOR bump and needs
   all three to agree

Until step 4, treat the diagrams as a working draft. Don't spend hours making them pretty.

---

## Enforcement

A `pre-commit` hook blocks any commit that touches a governed file without also staging
`VERSION.md`.

**Every teammate runs this once after cloning:**

```bash
git config core.hooksPath .githooks
```

That's in the setup steps in `README.md`. Without it the hook is inert — it is a convention with a
safety net, not a wall.

To bypass in a genuine emergency: `git commit --no-verify`. If you use it, add the entry in the next
commit.

---

# Log

Newest first.

## v1.3.1 - 2026-09-07 - Prachit (with Claude)
**Files:** `BUILD_PROMPTS.md`, `notion/stories.csv`
**Type:** PATCH - fixes drift introduced by the consolidation

Folding TASKS.md into BUILD_PROMPTS.md left the Tasks column in stories.csv pointing at T-xx ticket
ids that no longer existed, and the ticket index misnamed WU-12 as ImageSweep when it is the
reconciler. Fixed both, and added the six work units that had a story but no prompt.

- All T-xx references in `notion/stories.csv` remapped to WU-xx. Zero stale references remain
- **New work units:** WU-32 ImageSweep, WU-33 scheduled sweeps, WU-34 IAM and service accounts,
  WU-35 demo fixtures, WU-36 video and submission, WU-37 runtime evidence pack
- Ticket index rebuilt from WU-00 to WU-37 with owner and priority, plus the corrected cut order

Ownership: Prachit 13 work units, Vedant 25, Swara 2.

**Action required:**
- Swara: re-import `notion/stories.csv` again - the Tasks column changed
- Everyone: work units are WU-xx now. If a Claude session cites a T-xx id it is on a stale checkout

## v1.3.0 - 2026-09-07 - Prachit (with Claude)
**Files:** `notion/stories.csv`
**Type:** MINOR

The board predated the harness, Model Armor, observability, evaluation, the media sweeps and the
IAM design, so fourteen stories were missing. 47 -> 61 stories, 64h -> 87h.

- **New epic 0, agent harness** - S0.1 run every agent through one harness, S0.2 declare what each
  agent may touch. S0.1 blocks every other story in the project
- **New epic 11, evaluation** - S11.1 prove a hostile page cannot move a verdict, S11.2 prove the
  rule engine deterministically, S11.3 run evaluations on every commit
- Discovery gains S2.7 audio sweep, S2.8 video sweep, S2.9 download media safely
- Safe page reading gains S3.5 analyse media without capability, S3.6 Model Armor
- Resilience gains S8.5 trace a sweep end to end, S8.6 quality and security metrics
- Deployment gains S9.6 four isolated runtimes and S9.7 least privilege so evidence cannot be deleted
- S3.4 marked superseded by S3.6 and reduced to the UI badge; S9.3 corrected to Agent Runtime and
  cross-referenced to S9.6

**Action required:**
- Swara: re-import `notion/stories.csv` into a fresh Notion database rather than merging
- Prachit: 24 stories, 32h. **S0.1 first** - it blocks everyone
- Vedant: 33 stories, 51h. Heaviest load; if Monday slips, the P2s and S2.8 go first

## v1.2.1 - 2026-09-07 - Prachit (with Claude)
**Files:** `ARCHITECTURE.md`
**Type:** PATCH

- Added section 10, **Component reference** - every agent with its ADK type, tools, model and
  runtime, plus the tool table, Model Armor settings per path, the data and cache stores with their
  rules, the harness sequence, and the observability signals. The diagrams show flow; this table
  carries the technical detail, and it is authoritative if the two ever disagree.
- Four agents carry no model; three components carry no tools. Both are stated explicitly so nobody
  "fixes" them later.

## v1.2.0 - 2026-09-07 - Prachit (with Claude)
**Files:** all governed files
**Type:** MINOR - consolidation, no design change

Thirteen root markdown files reduced to eight. The sprawl was real: the same work
was described three separate times (a ticket list, a story list and a prompt list),
which was guaranteed to drift apart within a day. Every extra file is also context
a Claude session may load and another place a fact can go stale.

**Merged and deleted**
- `TECHNICAL_DESIGN.md` + `SYSTEM_DESIGN.md` -> **`DESIGN.md`** (Part I pipeline,
  Part II runtime and operations). These were always one document; the split only
  existed because a shell heredoc failed mid-session.
- `RESOURCE_MAP.md` -> **`COMPETITION.md` section 12**. Both answer "what the
  hackathon requires and offers".
- `TASKS.md` -> **`BUILD_PROMPTS.md`**, which gains a ticket index and the cut
  order. The work now lives in exactly two places with distinct jobs: this file
  for whoever is building, `notion/stories.csv` for Swara's board.
- `TEAM_BRIEF.md` and `BUILD_SPEC.md` -> **`README.md`**, rewritten to carry the
  plain-language explanation, the real litigation framing, and an explicit
  "what we do not claim" section.

**Content brought up to date with the design discussion**
- **WU-00, the agent harness, added as the first work unit and blocks everything
  else.** Built once, it gives all fourteen agents their reliability, security and
  observability; built per agent it would be inconsistent by Monday night.
- WU-01 rewritten for **Firestore, not SQLite** - findings use `url_hash` as the
  document id, which gives upsert idempotency for free.
- WU-19 rewritten for the decided cache stores: Firestore native TTL for small
  structured entries, GCS content-addressed for blobs, split at ~100 KB. No Redis.
- New work units: WU-26 AudioSweep, WU-27 VideoSweep, WU-28 fetch_media,
  WU-29 MediaTriage, WU-30 observability, WU-31 evalsets.
- Cross-references across every remaining file repointed to the merged docs.
- Team size corrected to three.

**Action required:**
- Everyone: the five deleted files are gone - if a Claude session cites one, it is
  working from a stale checkout. `git pull`
- Whoever starts first: **WU-00**. Nothing else should begin before it
- Prachit: WU-01 is Firestore now, not SQLite. Re-read it before starting

## v1.1.0 - 2026-09-07 - Prachit (with Claude)
**Files:** `DESIGN.md`
**Type:** MINOR

- **Discovery gains AudioSweep and VideoSweep** (both P1), making `DiscoveryAgent` a four-branch
  `ParallelAgent`. Voice cloning is sold as audio samples on marketplaces and synthetic
  endorsements run as video ads - text search finds the listing page but never confirms the
  offering is real.
- **The sweeps discover and download only; they never analyse.** A new `MediaTriage` agent inside
  `cn-triage` does the analysis with `tools=()`. The rule holds: every component reading untrusted
  content holds no capability. Downloaded media is untrusted exactly as a page is.
- New tool `fetch_media(url, max_bytes) -> MediaRef` - same SSRF guards as `fetch_page`, writes to
  the `derived/` bucket, returns uri, sha256 and mime.
- **Stated limit:** Gemini can transcribe and describe audio and video but cannot do speaker
  verification. Audio and video sweeps yield corroborating evidence that raises confidence, not
  identity proof. Say this on camera; it is the same discipline as NG-1.
- **Caching decided: no Redis or Memorystore.** Firestore `cache` collection with a native TTL
  policy for small structured entries; GCS `derived/` content-addressed by sha256 for large blobs;
  split at ~100 KB. Both are already provisioned, already in the IAM model, and shared across Cloud
  Run instances - an in-process LRU would be useless when the next request lands on a different
  container. `DEMO_MODE` warm cache now survives redeploys.
- Agent count 11 -> 14.

**Action required:**
- Vedant: AudioSweep and VideoSweep are P1, behind the P0 sweep path. ImageSweep drops to P2 below
  them - a voice-clone listing matters more than a caption-less image
- Prachit: cache work (WU-19) now targets Firestore TTL and GCS, not a local store

## v1.0.0 — 2026-09-07 — Prachit (with Claude) — 🔒 ARCHITECTURE FROZEN
**Files:** `consentinel/tools/contracts.py`, `consentinel/tools/__init__.py`, `ARCHITECTURE.md`,
`CLAUDE.md`, `BUILD_PROMPTS.md`
**Type:** MAJOR — frozen-contract change

T-07 answered against docs.parallel.ai. Both blocking questions closed, so the architecture is
frozen and **Swara is unblocked for design work**.

**Findings**
- **OQ-1 — locale is natively supported.** `location` takes an ISO 3166-1 alpha-2 country code;
  queries may be written in any language with no extra config (26+ languages, 30+ countries).
  `Locale.region` → `location`, `Locale.language` → the language of the query text. Our design maps
  across cleanly.
- **OQ-5 — Extract does NOT return full page content.** Compressed, objective-scoped excerpts only.
  It cannot replace `fetch_page` and cannot serve as evidence. Adopted instead as an optional **P1**
  cheap first-pass read during triage, with `fetch_page` reserved for dossier escalation.
- **OQ-2 — closed by decision.** No second partner product in v1. ClickHouse stays a post-hackathon
  path, so no component is added.

**Frozen-contract change — `tools/contracts.py`**
- `parallel_search` signature replaced. Was `(query: str, locale, limit)`. Now
  `(objective: str, search_queries: list[str], locale, max_results, mode, session_id)` returning
  `SearchResponse`. **The API takes 2–3 keyword queries of 3–6 words each per call, not one query.**
  Discovering this mid-build would have meant rewriting `QueryPlanner` and `TextSweep`.
- `SearchResult` now mirrors the real response: `url`, `title`, `publish_date`, `excerpts[]`, `raw`.
  The old `snippet`/`rank` fields did not exist.
- New `SearchResponse` wrapper: `search_id`, `results`, `warnings`, `session_id`.
- New optional `parallel_extract(urls, objective)`.

**Architecture changes (diagrams 2, 3, 5)**
- `parallel_extract` added as an optional first-pass reader; `fetch_page` retained on the escalation
  path. Extract output is untrusted content and sits inside the same boundary — the trust model is
  unchanged.
- Sequence diagram now shows batched queries and the `location` parameter.

**New: `DESIGN.md`** — the runtime and operational architecture that was missing.

- **Firestore only; SQLite dropped entirely.** Cloud Run's filesystem is ephemeral, so a SQLite
  registry would not survive between requests. `schema.sql` is now the *logical* model, Firestore
  the physical one, behind the unchanged `Store` interface. This simplifies WU-01.
- **Four Agent Runtime deployments**, six service accounts: `cn-enforcement`, **`cn-triage`
  isolated on its own** (the only component ingesting hostile content), `cn-clearance`, `cn-ingest`.
  One-runtime-per-agent was considered and rejected — eleven deploys, eleven cold starts, and no
  security gain over `tools=()` plus data-flow isolation.
- **IAM designed for one property:** no principal holds `objectAdmin` on the evidence bucket.
  Enforcement gets `objectCreator`, web gets `objectViewer`, triage gets no storage at all. With
  retention lock and versioning, *nothing in the system can delete evidence.*
- **The agent harness (WU-00) — build before any agent.** One wrapper giving all eleven agents
  trace spans, budgets, cache, Model Armor, schema validation, bounded repair, retry and circuit
  breaking, fail-safe resolution, audit and metrics. Each agent declares a `HarnessPolicy` whose
  `tools` tuple *is* the capability boundary, enforced in code.
- **Model Armor** replaces the hand-rolled regex injection canary. Configured per trust context:
  inspect-only on the triage path (we label injection rather than suppress a finding),
  inspect-and-block on dossier output (a notice must never carry PII or a malicious URL).
- **Observability**: audit log and traces are the same story at two granularities — audit is
  permanent and never sampled, traces are operational. Span tree, log rules, and 14 named metrics
  including `extraction.validation_failures` and `model_armor.detections`.
- **Evaluation**: five ADK evalsets. `adversarial_injection` asserts a hostile page cannot move a
  verdict; `verdict_matrix` and `clearance_matrix` are deterministic and free to run in CI.

**Scope note:** this session added roughly 15–20 hours to an estimate that was already 64. The
harness recovers much of it. Cut order if behind: ImageSweep → scheduler → the two soft evalsets →
the metrics dashboard. Never cut `adversarial_injection`, the four deployments, or the video.

**Action required:**
- **Swara: unblocked.** `ARCHITECTURE.md` is frozen at v1.0.0 — import and design from it. Please
  acknowledge the freeze
- **Vedant:** WU-04 is complete, do not redo it. Read the revised `parallel_search` signature —
  `QueryPlanner` must batch 2–3 short queries per call. Please acknowledge the freeze
- **Both engineers:** the **harness (WU-00) blocks everything**. Whoever starts first builds it

## v0.2.0 — 2026-09-07 — Prachit (with Claude)
**Files:** `BUILD_PROMPTS.md` (new), `CLAUDE.md`, `notion/`
**Type:** MINOR

- **`BUILD_PROMPTS.md`** — 25 copy-pasteable Claude Code prompts, one per work unit, covering the
  full P0 and P1 path. Each names the docs to read first, the file to write, the rules that bite on
  that specific piece, and its acceptance condition. This exists because our three Claude Code
  sessions share no context: `CLAUDE.md` loads automatically but cannot say *which* of fifty things
  to build now.
- **Notion board reworked** — `notion/stories.csv` replaces the 7-story list, which had the
  hierarchy inverted (10 epics cannot sensibly hold 7 stories). Now 47 stories, 3–6 per epic, each
  with a written description explaining what it is *and why it matters*, plus one acceptance
  condition. Added an `Assignee` field; statuses are Ready / In Progress / Done for Board view.
  `notion/user-stories.csv` deleted.
- `notion/README.md` — import steps, the View → Board → Group by Status switch, property types.

**Action required:**
- Vedant: start at **WU-04** in `BUILD_PROMPTS.md`. It blocks the entire agent side *and* the
  architecture freeze
- Prachit: start at **WU-01** (SQLite store), then WU-02
- Swara: import `notion/stories.csv`, switch to Board view, set the four property types

## v0.1.0 — 2026-09-07 — Prachit (with Claude)
**Files:** all governed files — initial creation
**Type:** MINOR

- **Frozen contract established:** `schema.sql` (7 tables), `store/base.py` (records, enums,
  `Store`/`Cache`/`EvidenceStore` interfaces), `tools/contracts.py` (`parallel_search`,
  `fetch_page`, `vision_web_detection` signatures, `TriageExtraction` schema)
- **`PRD.md`** — FR-1…FR-8 and TS-1…TS-6 with acceptance criteria, personas, non-goals, milestones,
  open questions
- **`DESIGN.md`** — fail-safe principle, scheduler, retry classification and circuit
  breaker, six guardrail layers, prompt-injection defence, storage zones, two cache regimes
- **`COMPETITION.md`** — verbatim rules, accepted SDKs, the Parallel runtime requirement, submission
  checklist, judging criteria, disqualification risks
- **`COMPETITION.md §12`** — every hackathon resource mapped to a component. Two decisions recorded:
  deploy agents to Agent Engine; generate demo fixtures with Imagen 3 and TTS so no real person's
  likeness appears in the submission
- **`ARCHITECTURE.md`** — 7 Mermaid diagrams
- **`BUILD_PROMPTS.md`** — 49 tickets across 10 epics, critical path, day plan, cut order
- **`README.md`**, **`README.md`**, **`README.md`**, **`CLAUDE.md`** hub
- `requirements.txt` corrected to the published ADK install line and the official `parallel-web` SDK

**Action required:**
- Everyone: run `git config core.hooksPath .githooks` after cloning
- Vedant: **T-07** first — it blocks the agent side *and* the architecture freeze
- Swara: import `BUILD_PROMPTS.md` and `ARCHITECTURE.md` into the Notion page; hold off on polished design
  until v1.0.0
