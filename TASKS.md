# Consentinel — backlog

Source of truth for *what needs doing*. Swara mirrors this into Notion; this file stays the
canonical copy because Claude Code sessions read it and Notion is not in their context.

**Team:** Prachit (foundation + app) · Vedant (agents + tools) · Swara (PM, docs, demo)
**Deadline:** 9 Sep 2026, 14:00 PDT. **We submit at 12:00 PDT.**

Status values: `todo` · `doing` · `blocked` · `done`
Priority: **P0** must ship · **P1** should ship · **P2** cut first

---

## Notion setup (Swara)

Create a database with these properties, then import the table below:

| Property | Type | Values |
|---|---|---|
| Task | Title | — |
| ID | Text | T-01 … |
| Epic | Select | Foundation, Discovery, Triage & Verdict, Enforcement Output, Clearance, Web App, Deploy & Ops, Demo & Submission, PM |
| Owner | Person | Prachit, Vedant, Swara |
| Status | Status | todo, doing, blocked, done |
| Priority | Select | P0, P1, P2 |
| Estimate | Number | hours |
| Depends on | Relation | self-referencing to this DB |
| Requirement | Text | FR / TS / TD reference |
| Acceptance | Text | how we know it's done |

Suggested views: **Board by Status** · **Board by Owner** · **Table filtered to P0** ·
**Timeline by day**.

Her Claude Code will need the **Notion connector** enabled to write there directly — otherwise she
pastes. Either is fine; the connector is faster if the setup takes under ten minutes.

---

## Epic 1 — Foundation (Prachit)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-01 | SQLite `Store` implementation, all interface methods | P0 | 3h | — | `store/base.py` | Every abstract method implemented; round-trips each record type |
| T-02 | Seed loader from `fixtures/seed.json` | P0 | 1h | T-01 | — | `python -m consentinel.seed` populates a fresh DB |
| T-03 | Cache: content-addressed regime (uploads, media, extractions) | P0 | 1.5h | — | TD §6.1 | Same file twice = one model call |
| T-04 | Cache: TTL regime, keys include `locale` + `prompt_version` | P0 | 1.5h | — | TD §6.2, FR-8.2 | Changing either key misses the cache; test proves it |
| T-05 | `EvidenceStore` — local FS for dev, GCS for deploy | P0 | 1.5h | — | FR-5.1, TD §5.2 | Snapshot written and retrievable by URI; no delete method exists |
| T-06 | `audit_append` helper recording `from_cache` + `cache_age_s` | P0 | 1h | T-01 | FR-7.1–7.3 | Every tool call produces an audit row with cache age |

## Epic 2 — Discovery (Vedant)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-07 | **Resolve OQ-1 + OQ-5** from Parallel's API reference: locale params, and whether Extract returns full page content | P0 | 0.5h | — | OQ-1, OQ-5 | Answers written into `CLAUDE.md`; T-08 and T-11 unblocked |
| T-08 | `parallel_search` tool via official `parallel-web` SDK, with call logging | P0 | 2h | T-07 | FR-2.3, FR-2.6, COMPETITION §4 | SDK imported and called; log shows query, locale, result count, timestamp |
| T-09 | `QueryPlanner` — queries × modality × locale, ≥5 locales / ≥4 languages | P0 | 2h | — | FR-2.1, FR-2.2 | Plan output includes non-English queries |
| T-10 | `TextSweep` + dedupe on `url_hash` | P0 | 1.5h | T-08, T-09 | FR-2.5 | Running a sweep twice does not duplicate findings |
| T-11 | `fetch_page` with SSRF guards, size caps, no credentials | P0 | 2h | T-07 | TD §4.4 | Private-range and non-HTTP URLs rejected; redirect re-validated |
| T-12 | `ImageSweep` via Cloud Vision web detection | **P2** | 2h | T-11 | FR-2.4, FR-2.7 | Reference image yields candidate pages |

## Epic 3 — Triage & verdict (Vedant)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-13 | `Triage` extractor using forced function calling → `TriageExtraction` | P0 | 2h | T-11 | FR-3.2, FR-3.3 | Output is always schema-valid or explicitly failed; never prose |
| T-14 | Guardrail validators: verbatim quote substring, ISO codes, name fuzzy-match, confidence range | P0 | 1.5h | T-13 | TD §3 L3 | A fabricated quote is rejected; test proves it |
| T-15 | `Reconciler` — deterministic rule engine, seven ordered checks | P0 | 2h | T-01 | FR-4.1, FR-4.2 | No model call in this module |
| T-16 | Unit tests for the rule engine: all branches + territory scoping | P0 | 1.5h | T-15 | FR-4 acceptance | Every branch covered incl. authorised-in-US / unauthorised-in-EU |
| T-17 | Retry + backoff wrapper, error classification, circuit breaker | P0 | 1.5h | — | TD §2 | 401 not retried; N consecutive failures marks sweep `degraded` |
| T-18 | Injection canary → `injection_suspected` flag + UI badge | P1 | 1h | T-13 | TD §4.3 | Planted injection page is flagged, not obeyed |

## Epic 4 — Enforcement output (Vedant)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-19 | Evidence snapshot capture — page text + screenshot | P1 | 2h | T-05 | FR-5.1 | Snapshot survives the source page being removed |
| T-20 | `DossierWriter` — evidence bundle + draft notice grounded in it | P1 | 2h | T-19 | FR-5.2, FR-5.3, FR-5.5 | Notice cites the specific clause; no send path exists |

## Epic 5 — Clearance (Prachit)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-21 | `AssetIngest` — content hash, read provenance metadata, record absence | P1 | 1.5h | T-01 | FR-6.2 | Missing metadata stored as a distinct signal, not null |
| T-22 | `PaperworkParser` — vendor invoice → declared generative usage | P1 | 1.5h | — | FR-6.3 | Declared AI usage extracted with a source quote |
| T-23 | `AssetInspector` — proxy transcode + Gemini multimodal | P1 | 2h | T-03 | FR-6.4, TD §5.3 | Proxy sent to model, not the original; transcript cached |
| T-24 | `ClearanceDecider` reusing T-15 + `ManifestBuilder` | P1 | 1.5h | T-15, T-21 | FR-6.5–6.7 | Two uploads give one `cleared`, one `blocked`; no-paperwork stays `unverified` |

## Epic 6 — Web app (Prachit)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-25 | App shell — FastAPI + Jinja, layout, nav | P0 | 1h | — | — | Serves locally |
| T-26 | Registry view — performers and their grants | P0 | 1.5h | T-01 | US-1 | Grants show clause quotes |
| T-27 | Findings queue — verdict badges, evidence quote, failing check | P0 | 2h | T-15 | FR-4.4, US-3 | Every row shows a citation and which check failed |
| T-28 | Decision-trail drawer — audit events + cache age | P1 | 1.5h | T-06 | FR-7.4, US-7 | Any verdict expands to inputs and their age |
| T-29 | Case-file view with copy-out | P1 | 1.5h | T-20 | FR-5.4 | Letter copyable; no send button anywhere |
| T-30 | Clearance board — production rollup + blockers | P1 | 1.5h | T-24 | FR-6.7, US-6 | Blockers listed before delivery |
| T-31 | Output escaping on all third-party text | P0 | 0.5h | T-27 | TD §4.4 | Script tags in page text render inert |

## Epic 7 — Consent ingestion (Prachit)

| ID | Task | Pri | Est | Depends | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-32 | `ConsentIngest` — contract PDF → grant with per-field citations, editable before save | P1 | 2.5h | T-01 | FR-1.1–1.5 | Upload produces a `consents` row; every field shows its source sentence |

## Epic 8 — Deploy & ops

| ID | Task | Pri | Est | Owner | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-33 | Deploy pipelines to **Agent Engine** | P0 | 2h | Vedant | RESOURCE_MAP, COMPETITION §5 | Agents invoked remotely, not in-process |
| T-34 | Deploy web app to **Cloud Run**, cold-start tested | P0 | 2h | Prachit | NFR-3 | Works from a fresh browser with no local server running |
| T-35 | Secret Manager wiring | P1 | 1h | Prachit | NFR-4 | No secrets in env files in deploy |
| T-36 | Cloud Scheduler + `/internal/sweeps/run-due` | **P2** | 1h | Vedant | TD §1 | Scheduled run appears in `audit_log` |

## Epic 9 — Demo & submission

| ID | Task | Pri | Est | Owner | Requirement | Acceptance |
|---|---|---|---|---|---|---|
| T-37 | Generate fixture media — Imagen 3 reference images + TTS cloned-voice clip | P0 | 1.5h | Swara | LE-1, RESOURCE_MAP | No real person's likeness anywhere in the submission |
| T-38 | Write the fictional performer's contract PDF | P0 | 1h | Swara | FR-1 demo input | Contains clauses that produce one `cleared` and one `blocked` |
| T-39 | `DEMO_MODE` cache warm + verify zero external calls | P0 | 1h | Vedant | FR-8.3 | Full sweep completes with network disabled |
| T-40 | Demo video script, timed to under 3:00 | P0 | 1.5h | Swara | COMPETITION §6 | Read-aloud timing verified, not estimated |
| T-41 | Record and edit the video | P0 | 2h | Swara | COMPETITION §6 | Public on YouTube/Vimeo, under 3:00 |
| T-42 | Devpost writeup incl. precedent citations | P0 | 1.5h | Swara | COMPETITION §7 | Names the SAG-AFTRA / Fortnite framing and the live cases |
| T-43 | Runtime-evidence screenshots — Parallel call log + GCP console | P0 | 0.5h | Vedant | COMPETITION §4 | Shows the integration called, not just named |
| T-44 | Final submission checklist pass | P0 | 0.5h | Swara | COMPETITION §6 | Every box ticked before 12:00 PDT |

## Epic 10 — PM (Swara)

| ID | Task | Pri | Est | Requirement | Acceptance |
|---|---|---|---|---|---|
| T-45 | Notion database set up and this backlog imported | P0 | 1h | — | Board by Owner view exists |
| T-46 | Port user stories US-1…US-7 from `PRD.md` into Notion, linked to tasks | P0 | 1h | PRD §6 | Each story links to its implementing tasks |
| T-47 | Architecture diagrams into Notion from `ARCHITECTURE.md` | P0 | 0.5h | — | All seven Mermaid diagrams render |
| T-48 | Twice-daily status sync: Notion → the Status section of `CLAUDE.md` | P0 | 0.5h ×4 | — | Claude sessions never rebuild finished work |
| T-49 | Track the four open questions to closure | P1 | 0.5h | CLAUDE.md | OQ-1, OQ-2, OQ-3, OQ-5 all answered or explicitly deferred |

---

## Critical path

```
T-07 → T-08 → T-10 → T-13 → T-14 → T-15 → T-27
```

**T-07 blocks the entire agent side.** Vedant does it first, before anything else.
`T-01 → T-02` blocks everything Prachit does. Both start immediately and independently.

## Day plan

**Sun 7 Sep** — T-07, T-01…T-06, T-08…T-11, T-13…T-17, T-25.
*End-of-day goal: real findings in the database with cited verdicts.*

**Mon 8 Sep** — T-19, T-20, T-21…T-24, T-26…T-32, T-33…T-35, T-37, T-38.
*Feature freeze at end of day. Deploy today, not tomorrow.*

**Tue 9 Sep, morning** — T-39…T-44. Submit at 12:00 PDT.

**Cut order if behind:** T-36 → T-12 → T-18 → T-30 → T-29 → T-28.
Stopping after T-27 still leaves a submittable demo.

## Estimate reality check

P0 alone is ~34h of work. Across three people over two days that is tight but feasible — provided
nobody gold-plates and the P2 items stay cut. If Monday evening is behind schedule, cut features,
never the video or the deploy.
