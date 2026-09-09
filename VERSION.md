# Version log

Change record for the **governed files** — the docs and the frozen contract. Every change to one of
them gets an entry here, in the same commit. A `pre-commit` hook enforces it (see
[Enforcement](#enforcement)).

Why: three people are working with separate Claude Code sessions that cannot see each other. This
file is how a session finds out that the contract moved since it last looked.

**Doc set version: `v2.4.0`**
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

## v2.4.0 - 2026-09-09 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus `web/` upload and confirm screens
**Type:** MINOR

**The contract reader now has a way in from the UI.** Upload a PDF, see what Gemini read with every
quote beside the field it supports, correct anything wrong, then save. Verified end to end against
the real contract: performer, licensee, US and CA, the 2026-2028 term, the payment trigger, 6 pages,
8 quotes, `voice_synth` and `archival_reuse` ticked and `face_replace` correctly left unticked. 80
tests.

**Actions are now gated; reads are not.** `web/security.py`. The three screens stay public because a
judge has to be able to open the URL, but reading a contract is a Gemini call and a sweep is a
Gemini call plus a Parallel call per query. On a public address with no gate a crawler can drain the
quota we need for the demo. The key travels as `?k=` and as a hidden form field. It is a shared
secret, not a login - it stops casual and accidental use, and should not be mistaken for identity.

**Two parts of WU-23 were deliberately not built**, because both would have been empty shells:
- the decision trail needs audit rows, and WU-18 has not been built, so the seeded findings have none
- the case file needs WU-16, which is Vedant's and not started
The clearance board part already existed on the `/clearance` screen.

**Known cosmetic point, left alone on purpose:** the citation shown is the model's transcription
rather than the document's own characters - Gemini returns curly quotes where the page has straight
ones. It renders correctly in a browser and the text is verbatim-equivalent. Snapping each quote
back to the page's exact bytes needs a normalised-to-original index map, which is not worth the
hours today.

**Action required:**
- Set `CONSENTINEL_ACTION_TOKEN` on the Cloud Run service before the demo, or the upload screen is
  open to the internet
- Vedant: `agents/common/gemini.py` has `generate_json`. Use it rather than building a client - and
  note the client must be held in a module global or it closes mid-request

## v2.3.0 - 2026-09-09 - Prachit (with Claude)
**Files:** `CLAUDE.md`, plus `consentinel/agents/` restructured
**Type:** MINOR

**Vedant: follow this layout for your agents.** `consent_ingest` was one 350-line file holding the
prompt, the schema, the guardrails, the policy and the logic. Split into the shape GridMind uses,
because six more agents are about to be written and the pattern should be right before they are.

```
agents/common/gemini.py     the only place we call a model
agents/<agent>/agent.py     the steps and the HarnessPolicy
agents/<agent>/guardrail.py what can reject an answer
agents/<agent>/instructions.py  the prompt, the schema, PROMPT_VERSION
```

- Prompts change far more often than code, so a wording tweak is now a one-file diff, and
  `PROMPT_VERSION` sits beside the words it versions.
- `guardrail.py` is the security surface of a step. A reviewer asking what stops a fabricated
  citation should find one short file.
- One `generate_json` in `agents/common/gemini.py` means the untrusted-content fence is drawn once
  rather than in every agent. No free-text variant exists on purpose.
- The package `__init__` re-exports everything, so callers and tests were unaffected.

**Bug found and fixed during the move:** building the Gemini client inline as
`_client().models.generate_content(...)` left nothing holding a reference, and it was closed while
the request was in flight - "Cannot send a request, as the client has been closed." It is now a
module-level cached client, which also stops auth being rebuilt on every call.

78 tests pass.

## v2.2.0 - 2026-09-08 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus `consentinel/agents/consent_ingest.py`, `tools/make_contract_pdf.py`
**Type:** MINOR

**WU-03 is done. Gemini is now genuinely in the product. 78 tests passing.**

Reading the demo contract: performer, licensee, US and CA, 2026-01-01 to 2028-12-31, the
per-title payment trigger, and eight citations - each one checked verbatim against the page it
claims. First attempt, no repair, 12.8 seconds.

**The result that matters:** it granted `voice_synth` and `archival_reuse` and did **not** grant
`face_replace`, because clause 10(a) withholds visual likeness. If it had read that withholding as
a grant, the blocked clip in the clearance demo would turn green and the whole point would collapse.
There is a live test asserting exactly that.

**Two real findings while building it**

- **A model re-typesets punctuation when it copies a sentence.** Gemini returned curly quotes for a
  page that renders them straight, so the verbatim check rejected a citation that was in fact a
  faithful copy - twice, including the repair attempt. `_norm` now folds quote and dash styling
  alongside whitespace. Both are how characters are *drawn*, not what they *say*. Nothing looser is
  tolerated, and a fabricated sentence still cannot match.
- The contract PDF used HTML curly-quote entities, which extracted as replacement characters and
  would have rendered as `?Territory?` on screen. Switched to `&quot;`.

**Design notes**
- Uncited fields are **dropped**, not kept. A permission record nobody can check is the thing this
  step exists to prevent.
- Nothing is written to the database. It returns a draft for a person to confirm, because a wrong
  permission slip silently poisons every later answer.
- A PDF with no readable text fails to `unverified` and says OCR is needed, rather than returning an
  empty but successful record.

**Action required:**
- Prachit: WU-23 needs an upload form and a confirm screen for this draft. Until then the extractor
  has no way in from the UI

## v2.1.1 - 2026-09-08 - Prachit (with Claude)
**Files:** `notion/stories.csv`, `notion/tasks.csv`
**Type:** PATCH

Statuses brought up to date, verified against main, the live URL and the open PRs.

**8 of 61 stories done, 2 in progress. 7 of 38 tasks done - 16.5 of 85.5 hours, about 19%.**
Prachit 6/13, Vedant 1/23, Swara 0/2.

The Notion board export had 67 rows for 43 unique cards, because the CSV was imported three times,
and the export had lost every property except Name and Epic. Regenerating from the repo rather than
trying to de-duplicate the board: the repo is canonical for *what a task is*, so a fresh import is
both correct and faster.

**Action required:**
- Swara: delete the existing board and import these two files into fresh databases. Do not merge
  into the current one - it has triplicate cards and no status, assignee or estimate columns

## v2.1.0 - 2026-09-08 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus `Dockerfile`, `.gcloudignore`, `requirements.txt`
**Type:** MINOR

**WU-25 is done. The app is live.**

    https://consentinel-web-255860737849.us-central1.run.app

Runs as its own service account, `consentinel-web@`, holding one role: Firestore read/write. No
secrets, no evidence writes. Cold hit 0.5s, warm 0.3s.

Three things this deploy uncovered that would have cost us tomorrow:

- **`google-cloud-firestore` was missing from `requirements.txt`** on this branch. Vedant had added
  it on his, so the container would have failed to import on first start. Caught by a dependency
  check before building, not by the build.
- **Cloud Run's frontend intercepts `/healthz`** and returns its own 404 before the request reaches
  the app. Our route was defined and visible in the app's own schema, yet unreachable. Renamed to
  `/_health`. Worth knowing before anyone configures a startup probe against a path that silently
  never arrives.
- **Cloud Build's default service account needed three roles** it did not have on a fresh project:
  storage.objectViewer, artifactregistry.writer, logging.logWriter. The first deploy failed on
  reading its own uploaded source.

`.gcloudignore` keeps tests, tools, docs, notion exports and the demo PDF out of the image.

**Action required:**
- Everyone: the URL above is public and read-only. There are no POST routes yet, so there is nothing
  to abuse. **The moment an action endpoint is added it needs a token**, or anyone can spend our
  Gemini and Parallel quota
- Swara: this is the URL for the submission form

## v2.0.0 - 2026-09-08 - Prachit (with Claude)
**Files:** `consentinel/store/base.py`, `consentinel/tools/contracts.py`, `consentinel/tools/__init__.py`, `DESIGN.md`
**Type:** MAJOR - frozen-contract change

**Vedant: read this before finishing WU-08.** The design defended against pages that try to
*manipulate* the agent. It did not defend against pages that are simply dangerous to open, or that
contain material we must not keep a copy of. Both are real for a system that searches the corners of
the web where cloned voices are sold.

**Contract changes**
- New tool `web_risk_check(url) -> UrlRisk` in `tools/contracts.py`. Called **before** `fetch_page`,
  never after. Uses Google's Web Risk service, which already keeps lists of malware and phishing
  sites, so we are not the ones finding out. If it says unsafe: do not open the page, and if the
  check itself fails, treat the address as unsafe and skip it.
- Two new `FindingStatus` values. `BLOCKED_UNSAFE` for an address we refused to open;
  `ESCALATED_UNLAWFUL` for a page carrying material we must not store.
- `fetch_page` docstring now states the order: web risk, then network guards, then Model Armor.
  Three different problems, and none of them substitutes for the others.

**Design addition - DESIGN.md Part III**
- Web Risk API enabled on the project.
- Model Armor settings stay asymmetric on purpose: **flag but do not block** on the way in, because
  a page trying to manipulate us is frequently the very page that is infringing and blocking it
  would suppress the finding. **Block** on the way out, because a takedown letter must never carry
  personal data or a link to a malware site.
- **The unlawful-material rule.** When safety filters flag a page in that category, nothing is
  snapshotted and nothing is rendered. We keep the address, a hash, the classification and the time,
  set `ESCALATED_UNLAWFUL`, and tell a person. This is the one place the product deliberately keeps
  *less* evidence, because here preserving is the harm.
- A refused page keeps `verdict = AMBIGUOUS`. Refusing to look is not the same as deciding the use
  was allowed.

**Action required:**
- **Vedant, WU-08:** call `web_risk_check` first. Do not fetch on an unsafe result and do not fetch
  when the check errors
- **Vedant, WU-06:** add known-bad hosts to Parallel's `source_policy.exclude_domains` - cheapest
  defence of all, since those pages never enter the pipeline
- **Prachit, WU-22:** the findings screen must render both new states without showing page content

## v1.8.0 - 2026-09-08 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus new code under `web/`
**Type:** MINOR

**WU-21 and WU-22 are done. There is something to look at. 62 tests passing.**

Three server-rendered pages reading live data out of Firestore:

- **Registry** - who we protect and what each studio may do, with the actual contract sentence and
  its page number shown underneath.
- **Found on the web** - the sweep results, ordered so breaches come first. Alphabetical order put
  "unclear" at the top, which buried the thing the page exists to show.
- **Our own footage** - the inward check, with a banner counting what cannot ship.

Three decisions worth knowing:

- **Raw values never reach the screen.** "unauthorized" is shown as "Not allowed", "unverified" as
  "Unchecked". A judge watching a video should not have to translate our database into English.
  There is a test that fails if a raw value leaks through.
- **Everything borrowed from someone else's website is escaped.** We display text from pages we do
  not control; rendering it as markup would let a stranger's page run script inside our app. A test
  feeds a finding containing a script tag and an image-onerror payload and checks neither can form.
- **The health check does not touch the database.** If it did, a slow database would look like a
  dead app and Cloud Run would restart the container for nothing.

The web tests use a fake in-memory store, so they need no network and no Google Cloud login - they
run in under a second.

**Action required:**
- Everyone: `pip install -r requirements.txt` again. The web dependencies are now needed
- Run it locally with `python -m uvicorn web.app:app --port 8080`
- Prachit: next is WU-25, getting this onto Cloud Run so there is a URL to submit

## v1.7.0 - 2026-09-08 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus `tools/make_contract_pdf.py`, `fixtures/docs/`, `requirements-dev.txt`
**Type:** MINOR

**The demo contract PDF exists. 54 tests passing.** This was part of WU-35 (Swara's) but it blocks
WU-03 consent ingestion, which is on the critical path, so it is done now.

`fixtures/docs/mira_vance_halcyon_agreement.pdf` - a six-page fictional performer agreement between
Mira Vance and Halcyon Pictures, generated by `tools/make_contract_pdf.py`.

- **Generated, not hand-made, for one reason:** WU-03's validator requires an extracted citation to
  be a verbatim substring of the page it claims to come from. The fixture cites two clauses at pages
  4 and 5, so the PDF has to put those exact sentences on those exact pages. A hand-made PDF would
  drift the first time anyone reflowed a paragraph, and the failure would look like a model problem.
- The contract states every field WU-03 must populate: licensee, performer, the 1 Jan 2026 to
  31 Dec 2028 term, United States and Canada as the territory, the archival-reuse grant, and the
  per-title compensation trigger.
- Clause 9 grants synthetic **voice** only, within the Territory. Clause 10 explicitly withholds
  synthetic **visual likeness**. That asymmetry is what makes the clearance demo work: asset_0412
  (AI voice) clears, asset_0533 (synthetic face) is blocked by clause 10(a).
- Every page is footed "SYNTHETIC DEMO DOCUMENT - fictional parties". Public repo, public video.
- `tests/test_fixture_contract.py` guards all of it: citations verbatim on their stated page, the
  six extractable facts present, visual likeness still withheld, synthetic marker on every page.

**Action required:**
- Swara: the contract half of WU-35 is done. Still outstanding are the Imagen 3 reference images and
  the TTS cloned-voice clip - `fixtures/media/` is still empty, and WU-17 clearance and WU-32
  ImageSweep have nothing to read until it is not
- Regenerating the PDF needs `pip install -r requirements-dev.txt` (reportlab)

## v1.6.1 - 2026-09-08 - Prachit (with Claude)
**Files:** `CLAUDE.md`
**Type:** PATCH

Three stale facts in the hub doc, which is the file every session loads:
- the stack line still said SQLite behind `Store`; it is Firestore, and Agent Engine is now called
  Agent Runtime
- a broken markdown link, `[COMPETITION.md §12](COMPETITION.md §12)`, left by the consolidation
- "two people are building this" - we are three

Second pass caught four more of the same kind: two more `Agent Engine` references (it is Agent
Runtime), another broken `COMPETITION.md §12` link, and a stale "SQLite behind the Store interface"
inside the OQ-2 answer. The consolidation left more dangling references than the first sweep found -
worth grepping for `SQLite`, `Agent Engine` and the old filenames if anything else looks off.

## v1.6.0 - 2026-09-08 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus `consentinel/seed.py` and a `doc_id_for` helper
**Type:** MINOR

**WU-02 the seed loader is done. 44 tests passing. The real registry now holds live data.**

Seeded into project `consentinel`: Mira Vance, the Halcyon Pictures grant (voice_synth and
archival_reuse, US and CA only), four findings covering all three verdicts including the pt-BR
territory-scoped one, and three assets covering cleared, blocked and unverified.

- `seed.py` reuses `from_doc` rather than hand-rolling JSON conversion, so the fixture decodes
  through exactly the same path Firestore reads take and cannot drift from the store's own decoding.
- **Found a real bug while testing.** `reset` deleted by `record.id`, which silently missed every
  finding, because a finding's document id is its `url_hash`. Fixed by adding `doc_id_for()` to
  `firestore_store.py` as the single source of that rule - anything addressing a document directly
  asks it rather than reimplementing the convention.
- `--reset` deviates from the work unit as written, which said "drop and recreate the schema" back
  when the store was SQLite. Firestore has no schema to drop and `purge()` refuses to run without a
  collection prefix, so `--reset` deletes exactly the documents the fixture defines and nothing else.
- Two tests guard the demo rather than the code: one fails if a fixture edit drops the pt-BR
  territory-scoped finding, one fails if the assets stop covering all three clearance states. Both
  would otherwise surface as a flat demo on the 9th.

**Action required:**
- Vedant: the registry has real data now. `WU-12` reconciler can be tested against the seeded grant
- Prachit: next is WU-18 (audit helper) then WU-19 (cache), which plug into the harness ports

## v1.5.0 - 2026-09-07 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus new code under `consentinel/store/`
**Type:** MINOR

**WU-01 the Firestore store is done. 36 tests passing, 11 of them against the real project.**

Infrastructure now live on project `consentinel`: Firestore database created in **us-central1**,
native mode (the location is permanent). APIs enabled: aiplatform, secretmanager, vision, run,
modelarmor, on top of firestore, cloudtrace, monitoring and storage.

- `store/codec.py` - one generic dataclass to document codec rather than six hand-rolled converter
  pairs. Handles enums, timezone-aware datetimes, Optional and lists. Unknown keys are ignored and
  missing keys fall back to defaults, so adding a field does not break reads of older documents -
  which is where hand-rolled converters silently drop data.
- `store/firestore_store.py` - two properties worth knowing. A finding's document id IS its
  `url_hash`, so upsert idempotency is free and two documents for one URL is structurally
  impossible. And `first_seen` survives a re-sweep while `last_checked` refreshes, because
  otherwise "when did we first see this" resets on every run and scheduled monitoring means nothing.
- `purge()` refuses to run without a collection prefix. An unprefixed purge would delete the real
  registry and audit trail, which is the exact operation the rest of the class exists to prevent.
- Tests run under a throwaway `test_<random>_` prefix and clean up after themselves. They assert
  that `audit_log` has no update or delete method by checking the attributes are ABSENT, and that
  re-appending an audit id is rejected.

**Action required:**
- Everyone: run `gcloud auth application-default login --project=consentinel` once. Client libraries
  need ADC; `gcloud auth login` alone is not enough
- Prachit: next is WU-02, the seed loader

## v1.4.0 - 2026-09-07 - Prachit (with Claude)
**Files:** `CLAUDE.md` (status), plus new code under `consentinel/harness/`
**Type:** MINOR - first code lands

**WU-00 the agent harness is done. Every other work unit is unblocked.**

- `harness/policy.py` - `HarnessPolicy` and `FailState`. Only three fail states exist and all three
  express doubt; there is deliberately no success value, so no failure path can produce `authorized`
  or `cleared`. The policy validates itself: content-addressed caches reject a TTL, and repair is
  capped at one attempt by construction.
- `harness/errors.py` - error classification, full-jitter backoff, circuit breaker. Unrecognised
  errors default to PERMANENT, because an unknown error retried three times costs three times as
  much and yields no more information.
- `harness/ports.py` - protocols for cache, audit, armor, tracing and metrics, with no-op defaults.
  The harness therefore does not block on WU-18, WU-19, WU-29 or WU-30; they plug in later without
  touching the runner.
- `harness/runner.py` - the twelve steps. `guard_tool` enforces the capability boundary and has no
  override parameter on purpose.
- 23 tests, all passing, including: an undeclared tool is refused, Triage with `tools=()` can call
  nothing, a 401 is not retried, validation gets exactly one repair, an injection is labelled rather
  than blocked on the way in while a malicious URL is blocked on the way out, and the breaker marks a
  sweep `degraded` rather than empty.

**Action required:**
- Vedant: unblocked. Every agent you write wraps in `Harness` and declares a `HarnessPolicy`
- Prachit: next is WU-01, the Firestore store

## v1.3.2 - 2026-09-07 - Prachit (with Claude)
**Files:** `notion/tasks.csv`, `notion/README.md`
**Type:** PATCH

Notion now has two related databases instead of one. A story says what should be true when we are
done; a task says what someone builds this afternoon. Several stories usually share one task, so
they are related rather than duplicated.

- `notion/tasks.csv` regenerated from the work units: 38 rows, WU-00 to WU-37, each with assignee,
  priority, estimate, dependencies, the stories it delivers, a description, an acceptance condition
  and a pointer to its prompt. The old file still used the dead T-xx ids and SQLite
- Every work unit links to at least one story, and every story links to a work unit
- The prompt TEXT is deliberately not copied into Notion. Claude sessions read the repo and cannot
  see Notion, so duplicating it would guarantee drift. Notion says what; the repo says how

**Action required:**
- Swara: import `tasks.csv` FIRST, then `stories.csv`, then wire the relations. Re-import into fresh
  databases rather than merging

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

