# Consentinel — Product Requirements Document

| | |
|---|---|
| **Product** | Consentinel |
| **Version** | 0.1 (hackathon v1) |
| **Date** | 7 September 2026 |
| **Owner** | Team Consentinel (2 engineers) |
| **Status** | Approved for build |
| **Target** | Agentic Cinema hackathon — Parallel track. Submission due 9 Sep 2026, 2:00 PM PDT |

---

## 1. Summary

Consentinel is a permission registry for AI copies of performers, plus two agent pipelines that
check the world against it.

> Consentinel finds people using an actor's face or voice without consent, and stops a studio from
> accidentally shipping something it never had the rights to.

One data spine — the consent registry — serves two opposite workflows: **enforcement** (find
unauthorised third-party use) and **clearance** (prove our own deliverables are covered).

---

## 2. Problem statement

Generative models can now reproduce a performer's face and voice at production quality. Contracts
have adapted: they now specify permitted synthetic uses, territories, durations, and compensation
triggers. Operational practice has not adapted. There is no system of record.

Two consequences:

**2.1 Unauthorised third-party exploitation.** Synthetic voice and likeness models of named
performers are openly sold and advertised. Rights holders — agencies, managers, estates — have no
systematic detection. Discovery is incidental and late, and each incident is researched manually.

**2.2 Unverifiable internal compliance.** A production is assembled by many vendors, any of whom may
have used generative tooling. Before delivery, someone must attest that the production holds rights
to all content. That attestation is currently assembled by email and assumption, with no auditable
trail.

Both reduce to a single unanswered question: **does a permission grant exist covering this use, in
this territory, at this time?**

---

## 3. Goals

| ID | Goal |
|---|---|
| G-1 | Convert unstructured consent language in contracts into a queryable registry |
| G-2 | Detect unauthorised commercial synthetic use of a registered performer on the open web |
| G-3 | Produce evidence-backed, citation-bearing verdicts a human can act on without re-research |
| G-4 | Establish coverage (not detection) for a production's own deliverables, default-deny |
| G-5 | Maintain a defensible audit trail for every decision, including the age of cached inputs |
| G-6 | Demonstrate deep, correct use of Gemini, Google Cloud Agent Builder/ADK, and Parallel Search |

## 4. Non-goals

Explicit, and stated on camera. A clean boundary scores better than an overclaim.

| ID | Non-goal | Rationale |
|---|---|---|
| NG-1 | Pixel-level deepfake forensics | Unsolved research problem; not required to deliver value |
| NG-2 | Platform-wide social monitoring (firehose ingestion, face/voice matching at scale) | Requires paid platform access; enterprise roadmap |
| NG-3 | Automated sending of legal notices | Unacceptable authority for an agent; humans send |
| NG-4 | Legal advice or determination of infringement | Consentinel surfaces evidence and rule mismatches; counsel decides |
| NG-5 | Multi-tenant accounts, auth, billing | Out of scope for v1 |
| NG-6 | Detection of non-commercial fan content | Low value, high false-positive cost, weak claim |

---

## 5. Users

### 5.1 Persona A — Rights protector (primary for the demo)

Talent agent, agency counsel, or estate manager. A performer's name, image and voice are the
revenue-generating asset. Currently learns about misuse by accident and researches each case by
hand.

**Needs:** continuous detection; a ready-to-action case file; certainty about which grant was
breached and where.

### 5.2 Persona B — Delivery gatekeeper

Studio business affairs counsel or post-production supervisor. Must attest the production holds all
necessary rights before delivery. Personally exposed if wrong.

**Needs:** a per-asset coverage status; a blockers list before delivery, not after; an auditable
record.

### 5.3 Persona C — Acquirer (secondary, pitch only)

Streamer or distributor acquiring a finished title. Requires proof that synthetic elements were
cleared. Consentinel's manifest is the artefact handed over at the point of sale — the moment the
product becomes mandatory rather than optional.

---

## 6. User stories

| ID | Story |
|---|---|
| US-1 | As a rights protector, I upload a signed agreement and get a structured permission record I can verify against quoted source text |
| US-2 | As a rights protector, I trigger a sweep for a performer and receive a list of suspected unauthorised uses |
| US-3 | As a rights protector, I see for each hit whether it is allowed, not allowed, or unclear — and which specific check failed |
| US-4 | As a rights protector, I generate a case file with preserved evidence and a draft notice, and send it myself |
| US-5 | As a delivery gatekeeper, I upload a deliverable and learn whether a permission grant covers it |
| US-6 | As a delivery gatekeeper, I see a production-level blockers list before delivery |
| US-7 | As either user, I can inspect the full decision trail for any verdict, including what data it used and how old that data was |

---

## 7. Functional requirements

### FR-1 Consent ingestion (US-1)

| | |
|---|---|
| FR-1.1 | Accept a contract PDF upload |
| FR-1.2 | Extract text and, via a single schema-constrained Gemini call, populate: performer, licensee, permitted uses (`voice_synth`, `face_replace`, `full_replica`, `archival_reuse`), territories (ISO 3166-1 alpha-2 or `WORLDWIDE`), `valid_from`, `valid_to`, `compensation_trigger` |
| FR-1.3 | For every populated field, return the verbatim source sentence and page number |
| FR-1.4 | Present extraction as an editable form; require explicit human save |
| FR-1.5 | Persist one `consents` row on save |

**Acceptance:** a PDF upload produces a `consents` row whose every field displays a source quote.

### FR-2 Discovery (US-2)

| | |
|---|---|
| FR-2.1 | Generate a query plan: performer name and aliases × modality terms × locale |
| FR-2.2 | Plan must cover ≥5 locales spanning ≥4 languages |
| FR-2.3 | Execute each query against the Parallel Search API at runtime |
| FR-2.4 | Reverse-image discovery via Cloud Vision web detection from `reference_images` (see FR-2.7) |
| FR-2.5 | Deduplicate candidates on `url_hash`; re-running a sweep must not create duplicate findings |
| FR-2.6 | Log every Parallel call with timestamp, query, locale, and result count |
| FR-2.7 | FR-2.4 is the lowest-priority requirement in the build and may be cut |

**Acceptance:** one sweep yields a deduplicated candidate list and a visible log of Parallel calls.

### FR-3 Triage — page reading (US-3)

| | |
|---|---|
| FR-3.1 | Fetch candidate page content |
| FR-3.2 | Produce a `TriageExtraction`: `depicts_named_person`, `person_name`, `is_synthetic_claim`, `modality`, `is_commercial`, `target_territories`, `evidence_quote`, `confidence` |
| FR-3.3 | Extraction output must be schema-constrained; no free-form text, no tool access |
| FR-3.4 | Infer `target_territories` from currency, language, shipping options, and stated jurisdiction |
| FR-3.5 | Run a text-only pass first; escalate to multimodal inspection only when the text pass is inconclusive |

**Acceptance:** every candidate has a stored `TriageExtraction` with a quote or an explicit null.

### FR-4 Reconciliation — the verdict (US-3)

| | |
|---|---|
| FR-4.1 | Verdict is produced by deterministic rule evaluation, not model judgement |
| FR-4.2 | Rules, in order: (a) no grant for performer → `unauthorized`; (b) modality not in `permitted_uses` → `unauthorized`; (c) any `target_territory` outside grant territories → `unauthorized`; (d) date outside validity window → `unauthorized`; (e) actor ≠ licensee → `unauthorized`; (f) `confidence` < 0.6 → `ambiguous`; (g) else `authorized` |
| FR-4.3 | Reconciliation input is the structured `TriageExtraction` plus registry rows only. **Raw page text must not be reachable from this step** |
| FR-4.4 | Every verdict must record the failing check, the matched grant (if any), and the evidence quote. Verdicts without a citation must be rejected |
| FR-4.5 | Verdicts are never cached; recompute on read |
| FR-4.6 | Territory-scoped verdicts must be expressible (e.g. authorised in US/CA, unauthorised in EU) |

**Acceptance:** the rule table is unit-tested; every displayed verdict shows a failing check and a citation.

### FR-5 Case file (US-4)

| | |
|---|---|
| FR-5.1 | Capture an immutable snapshot (text + screenshot) of the page at discovery, with no TTL |
| FR-5.2 | Assemble an evidence bundle: URL, snapshot URIs, evidence quote, breached clause citation |
| FR-5.3 | Generate a draft notice via Gemini, grounded strictly in the bundle |
| FR-5.4 | Present the draft as editable text with copy-out |
| FR-5.5 | **No send capability may exist in the product** |

**Acceptance:** a case file names the specific clause it relies on and offers no send action.

### FR-6 Clearance (US-5, US-6)

| | |
|---|---|
| FR-6.1 | Accept an asset upload, optionally with vendor paperwork |
| FR-6.2 | Read embedded provenance metadata where present; record absence as a distinct signal |
| FR-6.3 | Parse vendor paperwork for declared generative usage |
| FR-6.4 | Inspect the asset with Gemini multimodal for recognisable performer and human voice |
| FR-6.5 | Evaluate using the **same rule engine as FR-4.2** |
| FR-6.6 | Default state is `unverified`. `cleared` requires a matched grant; absence of evidence never yields `cleared` |
| FR-6.7 | Produce a production-level rollup with a blockers list |

**Acceptance:** two uploads produce one `cleared` and one `blocked`; an asset with no paperwork remains `unverified`.

### FR-7 Audit (US-7)

| | |
|---|---|
| FR-7.1 | Append an `audit_log` event for every agent step and tool call |
| FR-7.2 | Each event records inputs, tool calls, output, and `prompt_version` |
| FR-7.3 | Each tool call records whether it was served from cache and the cache age in seconds |
| FR-7.4 | The decision trail for any finding or asset must be viewable in the UI |

**Acceptance:** any verdict can be expanded to show its inputs and the age of each input.

### FR-8 Caching and demo resilience

| | |
|---|---|
| FR-8.1 | Cache search results, page fetches, image matches and LLM extractions with per-type TTLs |
| FR-8.2 | Cache keys include `locale` for web-facing calls and `prompt_version` for model calls |
| FR-8.3 | `DEMO_MODE=true` must run the full pipeline from cache with zero external calls |

**Acceptance:** with the network disabled and a warm cache, a full sweep completes.

---

## 8. Data model

Defined in `schema.sql`; typed records and interfaces in `consentinel/store/base.py`.

`performers` · `consents` · `findings` · `assets` · `dossiers` · `audit_log`

These, plus `consentinel/tools/contracts.py`, constitute a **frozen contract**. No field, enum or
signature changes without both engineers agreeing — the two halves of the build are coded against it.

---

## 9. Architecture

```
                        ┌─────────────────────────┐
                        │   CONSENT REGISTRY      │
                        └────────┬───────┬────────┘
        ENFORCEMENT              │       │              CLEARANCE
   ┌─────────────────────────────┘       └──────────────────────────────┐
1. QueryPlanner                                          1. AssetIngest
2. Discovery: TextSweep ∥ ImageSweep                     2. PaperworkParser
3. Triage (read page → structured answers)               3. AssetInspector
4. Reconciler (deterministic rules)  ◄── shared engine ──► 4. ClearanceDecider
5. DossierWriter                                         5. ManifestBuilder
   └──────────────► audit_log (append-only) ◄──────────────┘
```

Pipelines are composed with ADK `SequentialAgent` / `ParallelAgent` rather than LLM routing;
deterministic composition is materially more reliable to demo.

**Stack:** Python 3.11+ · Google ADK · Gemini on Vertex AI · Parallel Search API ·
Cloud Vision web detection · SQLite behind the `Store` interface · FastAPI + Jinja · Cloud Run.

---

## 10. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | A sweep of ≤25 candidates completes in under 3 minutes warm |
| NFR-2 | Storage engine is swappable via the `Store` interface without touching agent code |
| NFR-3 | Hosted deployment must be reachable from a cold URL with no local dependencies |
| NFR-4 | Secrets are supplied by environment only; the repository is public and must contain none |
| NFR-5 | Idempotent sweeps: repeated runs update findings in place rather than duplicating |
| NFR-6 | Multimodal inspection is invoked only on escalation, to control cost |

## 11. Trust and safety requirements

These are product requirements, not implementation notes. Each is also a pitch asset: the system
handles adversarial input with deliberately bounded authority.

| ID | Requirement |
|---|---|
| TS-1 | Fetched third-party content is treated as data, never instructions. It is passed in a delimited field and never composed into a system prompt |
| TS-2 | The reconciler is isolated from raw page content (FR-4.3), so injected text cannot influence a verdict |
| TS-3 | Extraction agents have no tool access and no free-form output channel |
| TS-4 | No component may perform an outward action. Notices are drafted only (FR-5.5) |
| TS-5 | Low-confidence results route to human review rather than being resolved by guess |
| TS-6 | Cache age is disclosed in the audit trail; a decision must never imply freshness it does not have |

## 12. Legal and ethical constraints

| ID | Constraint |
|---|---|
| LE-1 | Demo data uses a fictional performer and licensee. The repository and video are public; no authorisation verdicts about real individuals may be published |
| LE-2 | Live sweeps demonstrated on camera target a category, with third-party identifiers redacted |
| LE-3 | Output is framed as evidence and rule mismatch, never as a legal determination (NG-4) |
| LE-4 | Discovery uses documented APIs with locale parameters; no circumvention of access controls |

---

## 13. Success criteria

**Submission (binary — all must hold)**
- Hosted URL functional from a cold start
- 3-minute demo video, English or subtitled
- Public repository with complete license
- Evidenced runtime use of Google Cloud and Parallel
- Submitted before 12:00 PDT 9 Sep (2 hours of buffer)

**Demo quality (judged 25% each)**
- *Technological implementation:* ADK-native pipelines; Parallel and Gemini both load-bearing
- *Design:* a complete workflow with an artefact at the end, not a chat box
- *Potential impact:* an articulable buyer and a quantifiable manual task replaced
- *Quality of idea:* territory-scoped verdicts and the shared rule engine as evidence of domain depth

**Product hypotheses to validate post-hackathon**
- Sweep precision on commercial listings ≥80% on a hand-labelled set
- Case-file preparation time reduced from hours to minutes
- Coverage completeness: proportion of a production's assets reaching a non-`unverified` state

---

## 14. Milestones

| Date | Deliverable | Owner |
|---|---|---|
| 7 Sep AM | Frozen contract: schema, interfaces, tool signatures, fixtures | ✅ done |
| 7 Sep | Store implementation + seed loader | B |
| 7 Sep | `parallel_search` + `TextSweep` (FR-2) | A |
| 7 Sep | Cache layer (FR-8) | B |
| 7 Sep EOD | `Triage` + `Reconciler` (FR-3, FR-4) — findings in DB with cited verdicts | A |
| 8 Sep | Findings UI + decision-trail view (FR-7.4) | B |
| 8 Sep | Consent ingestion (FR-1) | B |
| 8 Sep | Case file (FR-5) | A |
| 8 Sep | Clearance, slim (FR-6) | B |
| 8 Sep EOD | Cloud Run deploy; **feature freeze** | A |
| 9 Sep AM | Demo video, Devpost writeup, evidence screenshots | both |
| 9 Sep 12:00 PDT | Submit | both |

`ImageSweep` (FR-2.4) is built only if 8 Sep runs ahead of schedule.

## 15. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Parallel API surface differs from assumption (esp. locale params) | Medium | Verify against their docs first thing; language variation works as a territory proxy without geo params |
| Google API names in `contracts.py` are stale | Medium | Verify ADK / Gemini / Vision surfaces against current docs before building |
| Live sweep returns thin or irrelevant results during the demo | High | `DEMO_MODE` cache (FR-8.3); warm the cache and record against it |
| Merge conflicts between two AI-assisted engineers | High | Split by file, not feature; frozen contract; `main` with frequent small commits |
| Scope creep into deepfake detection | High | NG-1 is explicit and stated on camera |
| Secret committed to a public repo | High | `.env` gitignored before first commit; rotate on any exposure |
| Cloud Run deploy discovered broken on the last morning | High | Deploy on 8 Sep, not 9 Sep |

## 16. Open questions

| ID | Question | Owner | Needed by |
|---|---|---|---|
| OQ-1 | Which region/language parameters does Parallel's Search API expose? | A | 7 Sep AM |
| OQ-2 | Do the hackathon rules permit using a second partner's product (e.g. ClickHouse) alongside the chosen track? | either | 7 Sep |
| OQ-3 | Current Cloud Vision web-detection API surface and quota | A | 8 Sep |
| OQ-4 | Screenshot capture approach for evidence snapshots on Cloud Run | A | 8 Sep |

## 17. Post-hackathon roadmap

1. Platform firehose ingestion with face and voice matching (lifts NG-2)
2. Continuous scheduled monitoring with alerting, replacing on-demand sweeps
3. Multi-tenant accounts, roles, and agency-level dashboards
4. Rights-holder portal: performers view and manage their own grants
5. Delivery-manifest export in the formats acquirers actually require
6. ClickHouse migration for the findings and audit tables at catalogue scale
