# Build prompts

Copy-pasteable prompts for Claude Code, one per work unit. Each is self-contained: it names the
docs to read first, the file to write, the constraints that apply, and the condition that means it
is done.

**Why this exists:** three people are running separate Claude Code sessions that share no context.
`CLAUDE.md` loads automatically, but it cannot tell a session which of fifty things to build right
now, or which specific rules bite on that piece. These prompts close that gap.

**How to use:** find your work unit, paste the fenced block into Claude Code, let it work, then
check the acceptance line yourself before marking the story Done.

**Before your first prompt of the session:** `git pull`, then read the newest entries in
[VERSION.md](VERSION.md).

---

# Epic 1 — Consent Registry

## WU-01 · SQLite Store  ·  Prachit  ·  T-01  ·  S1.1

```
Read CLAUDE.md, schema.sql and consentinel/store/base.py first.

Implement consentinel/store/sqlite_store.py: a SQLiteStore class implementing the
Store ABC from store/base.py. Every abstract method, no stubs.

Rules that bite here:
- audit_log is append-only. Implement append_audit and list_audit; do NOT add
  update or delete for it. The interface omits them deliberately.
- upsert_finding is idempotent on url_hash: a second call with the same hash
  updates the existing row and refreshes last_checked, never inserts.
- JSON columns (aliases, territories, permitted_uses, clause_citations,
  target_territories, provenance_metadata, tool_calls) serialise on write and
  deserialise into the dataclass types on read.
- Enums round-trip as their string values.
- Read the connection string from CONSENTINEL_DB_URL; create the schema from
  schema.sql if the database is empty.

Do not change schema.sql or store/base.py — they are a frozen contract shared
with the agent side. If something genuinely does not work, stop and say so
rather than editing them.

Done when: every record type round-trips through save and load with fields
intact, and calling upsert_finding twice with one url_hash leaves one row.
Write a small test proving both.
```

## WU-02 · Seed loader · Prachit · T-02

```
Read fixtures/seed.json and consentinel/store/base.py.

Write consentinel/seed.py, runnable as `python -m consentinel.seed`. It loads
fixtures/seed.json into the store: performers, consents, findings, assets.
Idempotent — running it twice must not duplicate anything.

Add a --reset flag that drops and recreates the schema first.

Done when: a fresh database, seeded, contains 1 performer, 1 consent,
4 findings and 3 assets, and running it a second time changes nothing.
```

## WU-03 · Consent ingestion · Prachit · T-32 · S1.2, S1.3, S1.4

```
Read CLAUDE.md, PRD.md FR-1, and consentinel/store/base.py (the Consent record).

Build consentinel/agents/consent_ingest.py: a contract PDF becomes a draft
Consent record.

1. Extract text per page with pypdf, keeping page numbers.
2. One Gemini call using forced function calling (see the Forced Function
   Calling resource in RESOURCE_MAP.md) returning: performer name, licensee,
   permitted_uses (subset of voice_synth/face_replace/full_replica/
   archival_reuse), territories (ISO 3166-1 alpha-2 or WORLDWIDE), valid_from,
   valid_to, compensation_trigger.
3. For EVERY populated field also return the verbatim source sentence and its
   page number, into clause_citations.
4. Validate: each citation must be a verbatim substring of that page's text.
   Drop any field whose citation fails — do not repair it silently.
5. Return a draft. Do NOT write to the database.

Temperature 0. Cache the extraction on sha256(file) + prompt_version, which
means re-running the same PDF costs nothing (TECHNICAL_DESIGN.md 6.1).

The human save step is the web layer's job, not this module's.

Done when: a PDF produces a draft with all six fields attempted and every
populated field carrying a verbatim quote plus page number.
```

---

# Epic 2 — Web Discovery

## WU-04 · Parallel API research · Vedant · T-07 · S2.1 — DO THIS FIRST

```
This blocks the entire agent side and the architecture freeze. Nothing else
should start before it.

Read https://docs.parallel.ai — the Search API reference and the Extract API
reference. Answer two questions:

OQ-1: Which region, country and language parameters does the Search API accept?
      Exact parameter names and accepted values. If none exist, say so — we
      fall back to language variation in the query text as a territory proxy.

OQ-5: Does the Extract API return full page content? If yes: what fields, what
      limits, what it costs. If it does, we should use it instead of our own
      fetch_page, so the partner service covers discovery AND retrieval at
      runtime, which strengthens the judged integration.

Also confirm the official Python SDK's package name, client construction and
the exact response shape, since we build tools/parallel_search.py against it.

Write the answers into the Open Questions section of CLAUDE.md with a link to
the page you got each from, add a VERSION.md entry, and commit.

If OQ-5 is yes, flag it clearly — it changes ARCHITECTURE.md diagrams 1, 2, 3
and 5, and the architecture freeze (T-50) depends on it.
```

## WU-05 · parallel_search tool · Vedant · T-08 · S2.3

```
Read CLAUDE.md, COMPETITION.md section 4, and consentinel/tools/contracts.py.
Do WU-04 first — its answers determine the locale parameters here.

Implement consentinel/tools/parallel_search.py against the signature already in
tools/contracts.py.

MUST use the official parallel-web Python SDK — imported and called. The
competition rules state that referencing Parallel in the README does not
satisfy the requirement and the integration must be present in code. Do not
substitute raw httpx calls to their endpoint.

- Map the Locale dataclass onto whatever parameters WU-04 found. If the API has
  no locale parameters, carry locale through into the query text and still
  include it in the cache key.
- Log every call: timestamp, query, locale, result count, latency. This log is
  submission evidence and gets screenshotted, so make it readable.
- Append an audit event per call including from_cache and cache_age_s.
- Cache on query + locale, 6-24h TTL.
- Wrap in the retry policy from TECHNICAL_DESIGN.md section 2 once WU-12 exists.

Done when: a search returns SearchResult objects and the call log shows the SDK
being invoked with query, locale and result count.
```

## WU-06 · QueryPlanner · Vedant · T-09 · S2.2

```
Read CLAUDE.md and PRD.md FR-2.1 and FR-2.2.

Build consentinel/agents/query_planner.py as an ADK LlmAgent producing a search
plan for a performer.

Input: a Performer record (name plus aliases) and their existing grants.
Output: a structured list of {query, locale, modality} — schema-constrained,
no prose.

- Cross the name and every alias with modality terms: AI voice, voice clone,
  voice model, AI avatar, deepfake ad, synthetic voice, and equivalents.
- At least 5 locales spanning at least 4 languages. Suggested: en-US, pt-BR,
  es-MX, ja-JP, hi-IN. Queries must be written IN the target language, not
  English text with a locale tag — a Portuguese listing is invisible to an
  English query.
- Cap the plan size so a sweep stays inside the 25-candidate budget.

Temperature 0.

Done when: the plan contains genuinely non-English queries across 5+ locales.
```

## WU-07 · TextSweep · Vedant · T-10 · S2.5

```
Read CLAUDE.md and PRD.md FR-2.5. Requires WU-05 and WU-06.

Build consentinel/agents/text_sweep.py: run every query in the plan through
parallel_search, collect results, and de-duplicate.

- url_hash = sha256 of the normalised URL (strip fragments, sort query params,
  lowercase the host). Normalisation matters or the same page arrives three
  times as three findings.
- Upsert findings through the Store, so re-running a sweep updates last_checked
  in place rather than inserting duplicates.
- Record discovered_locale on each finding — where we searched FROM. This is a
  different field from target_territories, which is where the offering is
  AIMED, and the triage step fills that one in.
- Concurrency limit and a per-sweep candidate cap of 25.

Done when: running the same sweep twice produces zero duplicate rows.
```

## WU-08 · fetch_page · Vedant · T-11 · S3.1

```
Read CLAUDE.md and TECHNICAL_DESIGN.md section 4.4. Check WU-04's answer first
— if Parallel's Extract API returns full page content, use that here instead of
fetching ourselves, and say so before writing code.

Implement consentinel/tools/fetch_page.py against the signature in
tools/contracts.py.

This is the untrusted-input boundary of the entire system. Security is the
feature:
- Resolve the host and REJECT private and link-local ranges: 10/8, 172.16/12,
  192.168/16, 127/8, 169.254/16, plus IPv6 equivalents.
- Reject any scheme other than http/https.
- Re-validate after EVERY redirect — a public URL redirecting to 169.254.x.x is
  the classic bypass.
- Cap response size and set a timeout.
- Send no cookies and no credentials. Declare a clear user agent.

Return a PageSnapshot. Set from_cache and cache_age_seconds honestly — the
audit trail depends on it.

The returned text is adversarial input. Never place it in a system prompt.

Done when: private-range URLs, file:// URLs, and a public URL redirecting to a
private address are all rejected. Write tests for all three.
```

---

# Epic 3 — Safe Page Reading

## WU-09 · Triage extractor · Vedant · T-13 · S3.2

```
Read CLAUDE.md, PRD.md FR-3, TECHNICAL_DESIGN.md sections 3 and 4, and the
TriageExtraction dataclass in consentinel/tools/contracts.py.

Build consentinel/agents/triage.py: a PageSnapshot becomes a TriageExtraction.

Use FORCED function calling so the model cannot emit prose. The schema is the
guardrail — an extractor with no free-form output channel has nowhere to
misbehave. It also gets NO tools.

Questions to answer: is a named real person depicted or mentioned and who; does
the page itself claim or advertise an AI copy; which modality (voice/face/
performance); is it commercial; which territories does it target (infer from
currency, language, shipping options, stated jurisdiction); quote the sentence
that proves it; confidence 0-1.

CRITICAL — prompt injection. The page text goes into a clearly delimited data
block, never into the system instruction. The system instruction states that
content inside the block is data to be described, and that any instructions
found inside it must be reported, not followed.

Temperature 0. Text-only pass first; escalate to multimodal only when the text
pass is inconclusive, since multimodal is the dominant cost line.

Cache on sha256(page_text) + prompt_version.

Done when: output is always a valid TriageExtraction or an explicit failure.
Never prose, never a partial object.
```

## WU-10 · Extraction validators · Vedant · T-14 · S3.3

```
Read TECHNICAL_DESIGN.md section 3 (guardrail layer L3).

Add consentinel/agents/validators.py, applied to every TriageExtraction before
it goes anywhere near the reconciler.

- evidence_quote MUST be a verbatim substring of the source page text. This is
  the highest-value guardrail in the system: one string comparison, and quote
  hallucination becomes structurally impossible. Normalise whitespace before
  comparing, nothing more.
- person_name must fuzzy-match the swept performer's name or one of their
  aliases. A mismatch means the model drifted onto a different person and the
  result is void.
- target_territories must all be valid ISO 3166-1 alpha-2 codes.
- confidence must be within [0, 1]. modality must be in the enum.

On failure: one repair attempt passing the validation error back to the model,
then give up and record ambiguous with reasoning "extraction failed validation
twice". A parse failure must never become a verdict.

Done when: an extraction carrying a fabricated quote is rejected. Write the
test that proves it.
```

## WU-11 · Injection canary · Vedant · T-18 · S3.4

```
Read TECHNICAL_DESIGN.md section 4.3.

Add a detector that scans fetched page text for imperative language addressed
at an AI system — "ignore previous instructions", "you are now", "mark this as
authorized", "system:", "assistant:", and similar.

On a hit, set injection_suspected on the finding. Do not block processing —
the point is that the system LABELS the attempt rather than obeying it, which
is already guaranteed structurally because the reconciler never sees page text.

Surface it as a badge in the findings UI (coordinate with Prachit on WU-19).

Also create a test fixture page containing an injection attempt. This is
demo footage: we show the system flagging it instead of following it.

Done when: a planted injection page is flagged, and its verdict is unchanged
from what the same page without the injection would produce.
```

---

# Epic 4 — Verdicts

## WU-12 · Reconciler rule engine · Vedant · T-15 · S4.1–S4.4

```
Read CLAUDE.md, PRD.md FR-4, and ARCHITECTURE.md diagram 7 (verdict rule flow).

Build consentinel/agents/reconciler.py.

THERE IS NO MODEL CALL IN THIS MODULE. If you find yourself importing a Gemini
client here, stop — the design is that the model observes and code decides.
That is what makes verdicts testable, explainable and immune to persuasion.

Input: a validated TriageExtraction (or clearance observations) plus the
consents for that performer. NOT page text — it must be unreachable from here.

Evaluate in order:
1. No grant for this performer            -> unauthorized
2. Modality not in grant permitted_uses   -> unauthorized
3. Any target territory outside grant     -> unauthorized
4. Date outside validity window           -> unauthorized
5. Actor is not the licensee              -> unauthorized
6. confidence < 0.6                       -> ambiguous
7. otherwise                              -> authorized

Every verdict records: the failing check, the matched or breached consent id,
and the evidence quote. REJECT any verdict lacking a citation — raise, do not
store it.

Territory scoping must be expressible: "licensed for US and CA, this listing
targets BR, unauthorized". Check 3 compares sets, not a single value.

Never cache verdicts — grants expire and get revoked, which would silently
invalidate a stored result. Recompute on read; the expensive inputs are already
cached.

Done when: no model call exists in the module, and every verdict names its
failing check and carries a citation.
```

## WU-13 · Rule engine tests · Vedant · T-16 · S4.6

```
Read PRD.md FR-4 and consentinel/agents/reconciler.py.

Write tests/test_reconciler.py covering every branch of the rule engine.

Required cases:
- no grant at all
- grant exists, wrong modality (voice grant, face use)
- grant exists, right modality, territory outside the grant
- expired grant, and a not-yet-effective grant
- third party rather than the licensee
- confidence below threshold -> ambiguous
- fully covered -> authorized
- TERRITORY SCOPING: one grant covering US and CA, an offering targeting both
  US and BR — must be unauthorized, and the reasoning must name BR
- a verdict attempted without an evidence quote must raise

This is the highest return per hour of testing in the project: the entire
product rests on this component, and it is deterministic, so it is cheap to
test properly.

Done when: all branches pass, including the territory case.
```

## WU-14 · Retry, backoff, circuit breaker · Vedant · T-17 · S4.5, S8.3

```
Read TECHNICAL_DESIGN.md sections 0 and 2.

Build consentinel/reliability.py and wrap every external call with it.

Classify before retrying:
- Transient (429, 500, 502, 503, timeout, connection reset): exponential
  backoff with full jitter, 3 attempts, cap 30s
- Permanent (400, 401, 403, 404): do NOT retry. Record and move on
- Semantic (schema violation): one repair attempt, then ambiguous

Circuit breaker: N consecutive failures from one provider aborts the sweep and
marks it degraded with a reason. Do NOT return a partial sweep that renders
like a complete one — "we found nothing" and "we could not look" must be
distinguishable in the UI.

THE GOVERNING RULE: every failure resolves toward doubt, never toward
permission. ambiguous, unverified, degraded — never authorized or cleared. A
compliance tool that fails quietly is worse than no tool, because someone signs
off on its output.

Log attempt number, latency and token usage into audit_log.tool_calls.

Done when: a 401 is not retried, and N consecutive failures mark the sweep
degraded rather than producing an empty clean-looking result.
```

---

# Epic 5 — Enforcement Output

## WU-15 · Evidence store and snapshots · Prachit (store) + Vedant (capture) · T-05, T-19 · S5.1

```
Read TECHNICAL_DESIGN.md section 5.2 and PRD.md FR-5.1.

Two pieces.

consentinel/evidence/store.py — implement EvidenceStore from store/base.py.
Local filesystem for dev, GCS for deploy, selected by CONSENTINEL_EVIDENCE_URI.
Returns a durable URI. Expose NO delete method.

consentinel/agents/snapshot.py — capture page text plus a screenshot at the
moment of discovery and write both through the evidence store. Set evidence_uri
on the finding.

THIS IS NOT THE CACHE. The infringing page will be taken down — that is the
entire point of a takedown — so these objects are immutable and have no TTL. If
a snapshot could expire, the case file would lose its proof exactly when it is
needed. In deploy, enable GCS object versioning and a bucket retention policy.

Screenshot on Cloud Run needs headless Chromium; if that turns into a rabbit
hole, ship text-only snapshots and note it. Text is the evidence that matters.

Done when: a snapshot is still retrievable after the source page is removed.
```

## WU-16 · Dossier writer · Vedant · T-20 · S5.2, S5.3, S5.4

```
Read PRD.md FR-5 and CLAUDE.md rule 6. Requires WU-15.

Build consentinel/agents/dossier_writer.py.

Assemble an evidence bundle: URL, snapshot URIs, evidence quote, the breached
clause with its citation, the verdict reasoning. Then generate a draft notice
with Gemini, grounded strictly in that bundle.

Validate the output: every URL and every quote appearing in the draft must be
present in the bundle. If the model introduces a fact that is not in the
bundle, reject and regenerate once.

The notice states evidence and rule mismatch. It does NOT assert legal
conclusions or offer legal advice (PRD non-goal NG-4).

NO SEND CAPABILITY. Not in this module, not in the UI, not anywhere. Draft and
copy-out only. An agent that can act on a mistaken verdict against a third
party is a liability. Do not add an email integration even if it seems helpful.

Done when: the case file names the specific clause it relies on, and no send
path exists anywhere in the codebase.
```

---

# Epic 6 — Delivery Clearance

## WU-17 · Clearance pipeline · Prachit · T-21 to T-24 · S6.1–S6.4

```
Read CLAUDE.md, PRD.md FR-6, and TECHNICAL_DESIGN.md section 5.3.
Requires WU-12 — you reuse its rule engine, you do not write a second one.

Build consentinel/agents/clearance/ with four steps.

1. ingest.py — content hash, read embedded provenance metadata (Content
   Credentials). Record ABSENCE as a distinct state, not null: a stripped
   credential is itself worth asking a vendor about.
2. paperwork.py — parse a vendor invoice or SOW for declared generative usage,
   with a source quote. Paperwork beats inspection and is far cheaper, so
   consult it first.
3. inspector.py — transcode a low-bitrate proxy and send THAT to Gemini, never
   the original; multimodal cost tracks bytes. Ask: recognisable performer
   present, human voice present. Store the transcript content-addressed so
   later questions answer from text with no second call.
4. decider.py — call the SAME reconciler from WU-12 with these observations.

DEFAULT IS unverified. An asset becomes cleared only when a grant covers what
was done to it. Absence of evidence is never clearance. We prove coverage; we
do not detect AI — which is exactly why imperfect detection is still useful.

Then manifest.py: a production-level rollup with a blockers list.

Done when: two uploads give one cleared and one blocked, and an asset with no
paperwork stays unverified.
```

---

# Epic 7 — Auditability

## WU-18 · Audit helper · Prachit · T-06 · S7.1, S7.2

```
Read PRD.md FR-7 and TECHNICAL_DESIGN.md section 6.

Build consentinel/audit.py: a helper every agent step and tool call uses to
append an AuditEvent.

Records: timestamp, actor (agent or tool name), subject type and id, inputs,
tool_calls, output, prompt_version.

Each entry in tool_calls MUST carry from_cache and cache_age_s. A decision must
never imply a freshness it does not have — claiming we checked the web at 3pm
when the data came from 9am undermines the one thing this product sells.

Make it a context manager or decorator so adding it to a new step is one line.
If it is awkward to use, people will skip it and the trail will have holes.

Append-only. No update, no delete.

Done when: every tool call produces an audit row carrying cache age.
```

---

# Epic 8 — Performance and Resilience

## WU-19 · Cache, both regimes · Prachit · T-03, T-04 · S8.1, S8.2

```
Read TECHNICAL_DESIGN.md section 6 in full. The two regimes are different and
conflating them is the bug.

Build consentinel/cache/ implementing the Cache ABC from store/base.py.

CONTENT-ADDRESSED — no TTL. An uploaded file never changes, so the hash IS the
key and the entry never goes stale.
  PDF text extraction:     sha256(file) + extractor version
  Contract -> consent:     sha256(file) + prompt_version
  Media inspection:        sha256(media) + prompt_version
  Transcripts:             sha256(media) + model version
  Reverse-image results:   sha256(image)

TTL-BASED — web content changes.
  Search results:  6-24h,  key: query + LOCALE
  Page fetches:    1-6h,   key: url + LOCALE
  Page-text extraction: 7d, key: sha256(page_text) + PROMPT_VERSION
  Negative results: 1h

Two keys are non-negotiable. Omit prompt_version and you will spend an hour
debugging output from a prompt you already deleted. Omit locale and you will
serve a cached US result for a JP query, quietly corrupting territory logic.

NEVER cache verdicts. They derive from a mutable registry.

get() returns a CacheEntry exposing fetched_at, because the audit trail must
record how old the data was.

Done when: the same file processed twice makes one model call, and changing
either locale or prompt_version misses the cache. Test both.
```

## WU-20 · DEMO_MODE · Vedant · T-39 · S8.4

```
Read PRD.md FR-8.3. Requires WU-19.

When DEMO_MODE=true, every external client — Parallel, Gemini, Cloud Vision,
page fetches — serves from cache only and raises a clear error on a miss rather
than reaching the network.

Add a warm-cache script that runs a full sweep and a full clearance check live,
populating everything the demo touches.

This is insurance. The video shoot cannot die on a rate limit at 1am the night
before submission, and this is roughly an hour of work against that.

Done when: with networking disabled, a full sweep completes end to end.
```

---

# Epic 9 — Deployment and Web App

## WU-21 · App shell · Prachit · T-25 · S9.1

```
Read CLAUDE.md.

Build web/ — FastAPI plus Jinja templates, with nav across three views:
Registry, Findings, Clearance.

Deliberately minimal. No auth, no multi-tenancy, no build pipeline, no frontend
framework. Server-rendered HTML and a little CSS. Every hour spent here is an
hour not spent on what is being judged.

Done when: it serves locally and you can navigate between the three views.
```

## WU-22 · Registry and Findings views · Prachit · T-26, T-27, T-31 · S1.5, S4.3, S9.2

```
Read PRD.md FR-4.4 and TECHNICAL_DESIGN.md section 4.4. Requires WU-12.

Registry view: performers with their grants — permitted uses, territories,
validity window at a glance, clause quotes on expand.

Findings queue: colour-coded by verdict. Each row shows the site, the evidence
quote, the verdict, and WHICH CHECK FAILED. A verdict with no citation should
be impossible to display, because it should be impossible to store.

Add the injection_suspected badge (coordinate with Vedant, WU-11).

SECURITY: every piece of third-party text — quotes, titles, URLs — is escaped
on output. Never render fetched HTML. We are displaying attacker-controlled
content inside our own UI, and half an hour here stops the demo executing
someone else's script on stage.

Done when: every findings row shows a citation and its failing check, and a
script tag inside page text renders inert.
```

## WU-23 · Decision trail, case file, clearance board · Prachit · T-28, T-29, T-30 · S7.3, S6.5

```
Requires WU-16, WU-17, WU-18.

Decision trail: any verdict expands to show its inputs, which tools ran, and
the AGE of each input. A user who cannot audit a decision will not rely on it.

Case file view: evidence bundle plus the draft notice in an editable box with
copy-out. NO SEND BUTTON — this is a hard product rule, not an oversight.

Clearance board: production rollup with the blockers list, each naming the
missing or non-covering grant.

Done when: a verdict expands to its inputs and their age; the case file has no
send action; blockers list per production.
```

## WU-24 · Agent Engine deployment · Vedant · T-33 · S9.3

```
Read RESOURCE_MAP.md and COMPETITION.md section 5. Follow the "Deploying ADK
Agents to Agent Engine" notebook linked from the hackathon resources page.

Deploy the enforcement and clearance pipelines to Agent Engine. The web app on
Cloud Run calls them remotely.

Why this and not raw API calls from a container: the brief says "powered by
Gemini and Google Cloud Agent Builder". Raw calls would satisfy the accepted-SDK
list but weakly satisfy the framing, and Technological Implementation is 25% of
the score.

Do this EARLY. Deployment problems discovered on the final morning are the
classic way a finished project fails to submit.

Done when: agents are invoked remotely, not in-process.
```

## WU-25 · Cloud Run and secrets · Prachit · T-34, T-35 · S9.4, S9.5

```
Read COMPETITION.md sections 5 and 6, and NFR-3 and NFR-4 in PRD.md.

Deploy web/ to Cloud Run. Secrets come from Secret Manager, not environment
files. The repository is public, so a committed key is both a security incident
and a competition risk.

TEST FROM A COLD START: open the hosted URL in a fresh browser with no local
server running and nothing cached. "It works on my machine" is not the
requirement; the judges get a URL.

Deploy on 8 September, not the 9th.

Done when: the hosted URL works from a fresh browser with nothing running
locally, and no secrets sit in environment files in the deployed service.
```

---

# Working agreements for every prompt

- **Stay in your lane.** File ownership is in CLAUDE.md. Do not refactor across
  the boundary; merge conflicts are the main risk with three AI-assisted people.
- **The frozen contract is frozen.** `schema.sql`, `store/base.py`,
  `tools/contracts.py`. Need a change? Say so, get agreement, then edit.
- **Governed file changed?** Add a `VERSION.md` entry in the same commit — the
  pre-commit hook will block you otherwise.
- **Update the Status section of `CLAUDE.md`** before you finish a session.
- **Temperature 0** everywhere except the draft notice.
- **Every model call gets a `prompt_version`** in its cache key.
- **Never commit a secret.** The repo is public.
