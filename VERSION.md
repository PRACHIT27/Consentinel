# Version log

Change record for the **governed files** — the docs and the frozen contract. Every change to one of
them gets an entry here, in the same commit. A `pre-commit` hook enforces it (see
[Enforcement](#enforcement)).

Why: three people are working with separate Claude Code sessions that cannot see each other. This
file is how a session finds out that the contract moved since it last looked.

**Doc set version: `v2.0.5`**
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

## v2.0.5 - 2026-09-09 - Vedant (with Claude)
**Files:** `CLAUDE.md` (status), plus new code under `consentinel/tools/`
**Type:** PATCH — implements contract v2.0.0; nothing new is declared

**WU-08 is done, both halves of it. 193 passing (60 new).**

`consentinel/tools/web_risk.py` and `consentinel/tools/fetch_page.py`. Both action items from
v2.0.0 are closed.

**Order of checks, exactly as the contract states it:** Web Risk, then the network guards, then
Model Armor (WU-29, on the triage path). A test asserts the *order* — a dangerous address is
refused for being dangerous, not for where it resolves, so the reason on the finding is the true one.

**It fails closed, and that is asserted rather than assumed.** If the Web Risk call errors, the
address comes back `safe=False` with `threats=["CHECK_FAILED"]`, distinguishable from a real
`MALWARE` verdict. `web_risk_check` never raises out — a safety check that throws is a safety check
somebody wraps in `try/except` and forgets.

**Two bugs the tests caught in my own first version**, both worth knowing if you write similar code:

- **IP-literal URLs skipped the guard.** `http://10.0.0.5/` went through the resolver instead of
  being checked directly. A literal is now checked as a literal — a broken or hostile resolver does
  not get to answer a question we can settle ourselves.
- **`100.64.0.0/10` is not `is_private` on Python 3.12.** Carrier-grade NAT sailed through. The
  guard now leads with `is_global` (IANA "globally reachable") and keeps the named ranges as well,
  because that property's definition has moved between versions and an SSRF guard should show
  10/8, 127/8 and 169.254/16 in the code.

Other things a reviewer should know:

- **Redirects are followed by hand**, and every hop is re-validated. The acceptance test is a public
  URL that 302s to `169.254.169.254`, with the handler asserting the private hop is never requested.
- **A refusal and a failure are different exception families.** `FetchRefused` (`UnsafeUrl`,
  `BlockedAddress`) means we declined; `FetchFailed` means we tried and could not. Both leave the
  verdict ambiguous, but only the first is a statement about the address. `fetch_detailed()` returns
  the same information without exceptions, plus `status=BLOCKED_UNSAFE` ready for the row.
- **No cookies, no credentials, `trust_env=False`** — that last one stops a proxy or `.netrc` in the
  environment attaching credentials to a request aimed at a hostile host. Declared user agent.
  `Accept-Language` is derived from the locale, so locale is on the wire as well as in the cache key.
- **HTML never gets rendered or escaped** — a stdlib `HTMLParser` drops tags on the way in and
  returns text plus absolute media URLs. One fewer dependency reading hostile input.
- Body is streamed and **truncated** at 2 MB with a marker in the text, because the other end
  chooses the size.
- `DEMO_MODE=true` serves cached pages and refuses to reach the network on a miss, same as
  `parallel_search`.

**WU-06/WU-07 follow-up (the other v2.0.0 action item):** `TextSweep` now passes
`source_policy.exclude_domains`, from `CONSENTINEL_EXCLUDE_DOMAINS` or an explicit argument.
**Empty by default on purpose** — an exclusion silently hides findings, so nothing is excluded
unless a person says so.

`requirements.txt`: added `google-cloud-webrisk`. `.env.example`: `WEB_RISK_ENABLED` (default true)
and `CONSENTINEL_EXCLUDE_DOMAINS`.

**Action required:**
- **Whoever owns the project:** enable the **Web Risk API** on `consentinel`. Until then every
  fetch fails closed and refuses every page — correct behaviour, useless demo. `WEB_RISK_ENABLED=false`
  is the local escape hatch and it logs loudly that pages went unchecked
- **Prachit, WU-22:** `FetchOutcome.status` is already `BLOCKED_UNSAFE` for refusals and
  `outcome.reason` carries the threat names, so the screen can say why without showing content
- **WU-09 (mine, next):** triage consumes `PageSnapshot.text` as data in a delimited field. Nothing
  from a page goes into a system prompt

## v2.0.4 - 2026-09-09 - Vedant (with Claude)
**Files:** `CLAUDE.md`, `COMPETITION.md` §12, `README.md`
**Type:** PATCH — a factual correction; nothing anyone is building against moves

**Imagen is not available on project `consentinel`.** Every `imagen-*` id returns 404 in both
`us-central1` and `global`, and none appear in `models.list()`. Three docs said the demo fixtures
were made with Imagen 3. Corrected to name what actually made them.

**The missing half of WU-35 now exists** — `tools/make_demo_media.py` (mirrors
`tools/make_contract_pdf.py`, idempotent, `--force` to regenerate):

- `fixtures/media/mira_ref_01.jpg` — 1408×768 JPEG, `gemini-3-pro-image` on the **`global`**
  endpoint. `seed.json` has pointed `perf_mira_vance.reference_images` at this path since day one and
  the file was never there; anything resolving it broke, and ImageSweep (WU-32) had no reference
  image to search with.
- `fixtures/media/NF_1042_ADR_v03.wav` — 6.9s, 24 kHz mono, `gemini-2.5-flash-tts`. Matches the
  `filename` on `asset_0412`, so the clearance demo has bytes rather than a string.

Worth knowing: **the Gemini 3 image models are `global`-only on this project** — they 404 in
`us-central1`, so the location travels with the model rather than coming from `.env`.
`gemini-2.5-flash-image` does work in `us-central1` if you need the configured region.
`gemini-2.5-flash-tts` works in `us-central1`.

`requirements-dev.txt`: added `pillow`. The image models return PNG and `seed.json` names a `.jpg`,
so the tool converts — a PNG wearing a `.jpg` extension works right up until something reads the
magic bytes.

**Action required:**
- Swara: WU-35's media half is done and the board card can move. Look at the headshot before it goes
  on camera — it is a plausible casting photo of nobody, but it is your call
- Anyone regenerating: `pip install -r requirements-dev.txt` first

## v2.0.3 - 2026-09-09 - Vedant (with Claude)
**Files:** `CLAUDE.md` (status), plus new code under `consentinel/agents/`
**Type:** PATCH — but read the DESIGN.md deviation below and push back if you disagree

*(Renumbered twice on merge, because three sessions numbered independently: these four entries
were written as v1.5.1–v1.5.4, then v1.7.1–v1.7.4, and are now **v2.0.1–v2.0.4** — above Prachit's
v2.0.0 contract change, which they are built on. Nothing was dropped; newest-first reads
highest-first again.)*

**WU-07 TextSweep is done. Epic 2's P0 path is complete: 114 tests passing (24 new).**

`consentinel/agents/text_sweep.py` runs a plan through `parallel_search`, deduplicates and upserts
findings. Same sweep twice → zero duplicate rows (FR-2.5).

**Deviation from DESIGN.md Part II 2.1, deliberate and not silently:** the table lists TextSweep as
an `LlmAgent` holding `parallel_search`. I built it **deterministic, with no model call.**
QueryPlanner already did the thinking; what remains is "call these eight batches, normalise, dedupe,
upsert", and a model choosing which of its own planned queries to run adds non-determinism to a live
demo for no coverage gain — which is the reason CLAUDE.md prefers deterministic composition. I did
not edit the frozen-ish design doc for this. If we want the table to be true, say so and it becomes
an `LlmAgent` whose tool loop runs the same batches; otherwise DESIGN.md's row should change and
that is a MAJOR bump someone else should approve.

What a reviewer should know:

- **`url_hash` = sha256 of the normalised URL**, and `normalise_url` is the whole of deduplication:
  lowercase scheme and host, drop the default port, drop the fragment, strip a trailing slash, sort
  query params and remove tracking ones (`utm_*`, `gclid`, `fbclid`, …). Deliberately **not**
  merged: `www.` with the bare host, and `http` with `https` — they are usually the same page and
  occasionally not, and a wrong merge silently hides a finding, which is worse than one duplicate a
  human can dismiss. Both are importable (`from consentinel.agents.text_sweep import url_hash`) so
  the UI and WU-08 hash identically.
- **`Finding.id == url_hash`.** Deterministic on purpose: the upsert stays idempotent whichever
  field a Store implementation keys on. A uuid here would create a second row per sweep.
- **`first_seen` / `last_checked` are left `None`** — the store owns them, and WU-01 already
  preserves `first_seen` across re-sweeps.
- **`discovered_locale` is set; `target_territories` and `modality` are not.** We searched a
  modality, we have not established the page depicts one. Triage fills those in by reading the page;
  asserting them here would put an unverified claim in the registry.
- **Excerpts are never persisted.** They ride in `SweepReport.candidates` for triage to prioritise
  with, because they are LLM-selected and truncated and evidence is our own snapshot (WU-15).
- **`SweepReport.degraded` is broad on purpose**: a degraded plan, one dead batch, or one failed
  write all set it. A dead batch does not kill the sweep — the other seven still land — and an empty
  *successful* sweep is explicitly not degraded, which is the distinction hard rule 10 exists for.
- Concurrency is 4 threads; results are collected in **plan order** regardless of completion order,
  so the 25-candidate cap is reproducible rather than a race. The cap sits in two places: the plan
  asks for `25 // batches` per batch, and the sweep drops any overflow if a provider ignores that.
- One audit row per sweep (`event: "sweep"`) on top of WU-05's row per call, and one summary log
  line.

**Action required:**
- Prachit: `url_hash` / `normalise_url` are the canonical implementation — import them rather than
  re-deriving in `web/`, or the UI and the registry will disagree about identity
- Whoever takes WU-08 (`fetch_page`): candidates arrive as `SweepReport.candidates`, each with
  `url`, `url_hash`, `locale` and excerpts
- Someone other than me should rule on the DESIGN.md deviation above

## v2.0.2 - 2026-09-09 - Vedant (with Claude)
**Files:** `CLAUDE.md` (status), plus new code under `consentinel/agents/`
**Type:** PATCH — no contract or requirement moved

**WU-06 QueryPlanner is done. 90 tests passing (31 new).**

`consentinel/agents/query_planner.py` — an ADK `LlmAgent` with `output_schema` set, temperature 0,
`tools=()`. Setting `output_schema` is what removes the free-form channel: ADK then refuses tools
and agent transfer, so the model can only fill in `PlanOut`.

Things worth knowing if you touch discovery:

- **The plan is a list of batches, not queries.** Each batch is one `parallel_search` call:
  `{objective, search_queries (2-3), locale, modality}`. `iter_search_calls(plan)` yields
  ready-made kwargs for WU-05, including `max_results` already divided by the batch count.
- **The 25-candidate budget is enforced in the plan**, not at collection time: 8 batches max,
  `results_per_batch = 25 // len(batches)`. A budget enforced only downstream is a budget already
  spent.
- **`MODALITY_TERMS` holds the search vocabulary per language** (en, pt, es, ja, hi × voice, face,
  performance). Add a language there and it becomes plannable everywhere. A grant territory whose
  language is missing from that table is *skipped, not searched in English* — an English query aimed
  at a French market finds nothing and looks like coverage, which is worse than an admitted gap.
- **Validation rejects English wearing a locale tag.** A ja/hi batch must contain non-ASCII, and no
  non-English batch may contain an English modality term ("voice clone", "deepfake ad", …). The
  model gets exactly one repair attempt, carrying the validator's own message.
- **A model failure degrades to `deterministic_plan(...)`** built from the vocabulary table, with
  `SearchPlan.degraded=True` and a reason. The sweep still runs and still meets FR-2.2 — a broken
  model costs plan quality, never coverage.
- Grants are context, never an exclusion list (hard rule 4): granted territories get *more*
  coverage, because permission in one territory and shipment in another is the thing we hunt.
- Cache key includes `prompt_version` (hard rule 8) and the instruction text, 24h TTL.

**Action required:**
- Whoever writes WU-07: consume `iter_search_calls(plan)`, and check `plan.degraded` — a degraded
  plan means the sweep should be reported as partial, not clean
- Prachit: nothing. This adds `consentinel/agents/`, touches nothing of yours

## v2.0.1 - 2026-09-09 - Vedant (with Claude)
**Files:** `CLAUDE.md` (Parallel SDK surface, status), plus new code under `consentinel/tools/`
**Type:** PATCH — the frozen `parallel_search` signature is unchanged

**WU-05 is done. 59 tests passing (36 existing + 23 new).**

The frozen signature in `tools/contracts.py` holds exactly as written. What moved is *where the
arguments go* once they reach the SDK, checked against the installed `parallel-web` 1.3.3 rather
than the doc prose:

- the call is `client.search(...)`, top level, not `client.beta.search`;
- `location`, `max_results` and `source_policy` are **not** top-level arguments — they live in
  `advanced_settings`. Only `search_queries`, `objective`, `mode`, `max_chars_total`, `session_id`
  and `client_model` are top level.

`consentinel/tools/parallel_search.py` owns that mapping, so nothing else needs to know it. Also
worth knowing about the implementation:

- It runs through the WU-00 harness, so retries, backoff, circuit breaking, TTL cache and fail-safe
  come from there. The SDK client is built with `max_retries=0` — two retry loops turn one rate
  limit into nine.
- **A failure returns a `SearchResponse` carrying a `consentinel_degraded` warning, not an empty
  result list.** Use `is_degraded(response)` / `degraded_reason(response)`. A sweep that could not
  look must not read like a sweep that looked and found nothing (hard rule 10).
- Cache key is queries + locale + everything else that changes the result set, TTL 6h. `session_id`
  is deliberately *out* of the key: it groups one sweep's calls on Parallel's side and would
  otherwise fragment the cache across sweeps asking identical questions.
- `DEMO_MODE=true` serves cache hits and refuses to reach the network on a miss (degraded).
- Every call writes one readable log line on `consentinel.parallel_search` plus an audit row with
  queries, locale, result count, latency, `from_cache` and `cache_age_s`. That log is the
  runtime-evidence screenshot — `enable_call_log()` turns it on from a plain script.
- `consentinel/tools/__init__.py` now exports the implementation, so `from consentinel.tools import
  parallel_search` gives the working tool rather than the contract stub.

`requirements.txt`: added `google-cloud-firestore`. WU-01's 13 store tests skip silently without it,
so a fresh clone saw 23 tests, not 36.

**Action required:**
- Whoever writes WU-06/WU-07: emit batches of 2-3 queries of 3-6 words and call
  `ParallelSearch.search_detailed(...)` if you need attempts or cache age for a partial-sweep
  report. Check `is_degraded` before treating an empty result set as "nothing out there"
- Everyone: `pip install -r requirements.txt` again to pick up `google-cloud-firestore`
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
