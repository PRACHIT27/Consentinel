# Consentinel — system design

Runtime placement, IAM, the agent harness, Model Armor, observability and evaluation.
Decided 7 Sep 2026. Companion to [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) (pipeline-level design)
and [ARCHITECTURE.md](ARCHITECTURE.md) (diagrams).

---

# 1. Data layer — Firestore only

**No local database.** SQLite is dropped entirely: Cloud Run's filesystem is ephemeral and instances
are replaced, so a SQLite registry would vanish between requests.

`schema.sql` remains the **logical** model. Firestore is the physical one, behind the same `Store`
interface — which is exactly why that interface exists.

| Collection | Contents | Notes |
|---|---|---|
| `performers` | name, aliases, reference images | |
| `consents` | the permission grants | |
| `findings` | doc id = `url_hash`, giving upsert idempotency for free | |
| `assets` | deliverables under clearance | |
| `dossiers` | evidence bundle + draft notice | |
| `audit_log` | append-only | write-once rules; no update, no delete |

Joins we would have done in SQL happen in Python — the data is small, and a sweep touches at most a
few hundred documents.

---

# 2. Runtime placement — four Agent Runtime deployments

One runtime per pipeline, plus **Triage isolated on its own** because it is the only component that
ingests hostile third-party content.

| Runtime | Contains | Service account | Trust context |
|---|---|---|---|
| `cn-enforcement` | QueryPlanner, TextSweep, ImageSweep, Reconciler, Dossier | `consentinel-enforcement@` | Internal; calls partner APIs |
| `cn-triage` | **Triage only** | `consentinel-triage@` | **Hostile input** |
| `cn-clearance` | AssetIngest, Paperwork, Inspector, Manifest | `consentinel-clearance@` | Internal assets |
| `cn-ingest` | ConsentIngest | `consentinel-ingest@` | User uploads |

Web UI runs on **Cloud Run** and invokes the four runtimes.

**Why not one runtime per agent (11 deployments):** each deploy takes minutes and would be repeated
on every change, ADK composes `SequentialAgent` in-process, and eleven network hops buy nothing that
`tools=[]` and data-flow isolation do not already give us. Deployment separation mitigates
credential theft — which is why Triage, and only Triage, gets its own.

## 2.1 The agents

| # | Agent | ADK type | Tools | Model | Runtime |
|---|---|---|---|---|---|
| 1 | QueryPlanner | `LlmAgent` | none | Gemini | enforcement |
| 2 | TextSweep | `LlmAgent` | `parallel_search` | Gemini | enforcement |
| 3 | ImageSweep | `LlmAgent` | `vision_web_detection` | Gemini · P2 | enforcement |
| 4 | **Triage** | `LlmAgent` | **none** | Gemini + Model Armor | **triage** |
| 5 | **Reconciler** | custom `BaseAgent` | registry read | **no model** | enforcement |
| 6 | Dossier | `LlmAgent` | evidence read/write | Gemini + Model Armor | enforcement |
| 7 | AssetIngest | custom `BaseAgent` | storage read | **no model** | clearance |
| 8 | Paperwork | `LlmAgent` | none | Gemini | clearance |
| 9 | Inspector | `LlmAgent` | none | Gemini multimodal | clearance |
| 10 | Manifest | custom `BaseAgent` | registry read | **no model** | clearance |
| 11 | ConsentIngest | `LlmAgent` | none | Gemini | ingest |

Composition — `SequentialAgent`, `ParallelAgent`, `LoopAgent` — wraps these and is not separately
deployed.

Three agents have **no model at all**. That is deliberate: the verdict, the manifest and asset
ingestion are decisions, and decisions are code.

---

# 3. IAM — least privilege

| Principal | Grants | Deliberately withheld |
|---|---|---|
| `consentinel-web@` (Cloud Run) | invoke the four runtimes · Firestore read/write · **evidence: objectViewer** | no evidence write · no Parallel key |
| `consentinel-enforcement@` | Vertex AI user · Firestore read/write · Vision API · **evidence: objectCreator** · Parallel key secret | **no objectAdmin — cannot delete or overwrite evidence** |
| `consentinel-triage@` | Vertex AI user · Model Armor · **Firestore read only** | no secrets · no storage · no Firestore write |
| `consentinel-clearance@` | Vertex AI user · Firestore read/write · uploads + derived buckets | no evidence bucket · no Parallel key |
| `consentinel-ingest@` | Vertex AI user · Firestore write · uploads read | no evidence · no partner APIs |
| `consentinel-scheduler@` | `run.invoker` on the internal sweep endpoint | nothing else |

**What this buys.** `objectCreator` (never admin) on enforcement, `objectViewer` on web, and no
storage grant whatsoever for triage — combined with bucket retention lock and object versioning,
**no principal in the system can delete evidence.** Not the agents, not the web app, not a stolen
token. That is a property a compliance product can genuinely claim, for about ten minutes of
`gcloud`.

Note that Triage — the one component processing attacker-controlled text — holds the weakest
permission set in the entire system. That is the design, not an accident.

Verify the exact role name for invoking Agent Runtime against current Google docs at deploy time.

---

# 4. The agent harness — build this first

**Every agent runs through one wrapper.** This is WU-00, before any agent exists. Implemented once,
it gives eleven agents their reliability, security and observability. Implemented per agent, it will
be inconsistent by Monday night and missing entirely in two places.

`consentinel/harness/` wraps each invocation, in order:

| # | Step | Detail |
|---|---|---|
| 1 | Open trace span | agent, `prompt_version`, sweep id, subject id |
| 2 | Budget check | per-call timeout; per-sweep wall clock and token ceiling |
| 3 | Cache lookup | content-addressed or TTL (TECHNICAL_DESIGN §6); records `cache_age_s` |
| 4 | **Model Armor** `SanitizeUserPrompt` | only on agents receiving untrusted content |
| 5 | Invoke | temperature 0; forced function calling wherever a schema is expected |
| 6 | **Model Armor** `SanitizeModelResponse` | only on agents producing outward-facing text |
| 7 | Validate | schema, then the field validators of TECHNICAL_DESIGN §3 L3 |
| 8 | Repair | exactly one retry with the validation error appended, then stop |
| 9 | Retry / classify / circuit-break | TECHNICAL_DESIGN §2 |
| 10 | **Fail-safe resolution** | unrecovered failure → `ambiguous` / `unverified` / `degraded`. Never `authorized`, never `cleared` |
| 11 | Audit append | inputs, tool calls, output, `prompt_version`, `from_cache`, `cache_age_s` |
| 12 | Emit metrics, close span | |

## 4.1 HarnessPolicy

Every agent declares one:

```python
@dataclass(frozen=True)
class HarnessPolicy:
    agent_name: str
    timeout_s: float
    max_attempts: int              # transient retries
    output_schema: type | None     # None for no-model agents
    cache: Literal["content", "ttl", "none"]
    cache_ttl_s: int | None
    armor_prompt: str | None       # Model Armor template, or None
    armor_response: str | None
    tools: tuple[str, ...]         # THE capability boundary
    fail_state: str                # "ambiguous" | "unverified" | "degraded"
```

`tools` is not documentation. **It is the capability boundary** — the harness refuses any tool call
not named in the policy, so the trust boundary is enforced in code rather than by convention. Triage
declares `tools=()`.

---

# 5. Model Armor

`SanitizeUserPrompt` / `SanitizeModelResponse`, configured through templates. Screens **prompt
injection and jailbreak**, responsible-AI safety, **sensitive data**, and **malicious URLs** — all
four relevant, since we ingest attacker-controlled pages and emit outward-facing legal text.

Settings differ by trust context. This is the point, not a detail:

| Path | Call | Action | Confidence | Why |
|---|---|---|---|---|
| Page text → Triage | `SanitizeUserPrompt` | **Inspect only** | Low and above | We want to *label* an injection attempt, not block it. A page trying to manipulate us is frequently the very page that is infringing — blocking would suppress the finding |
| Contract, paperwork → ingest | `SanitizeUserPrompt` | Inspect only | Medium and above | User-supplied, not hostile; PII detection is what matters |
| Dossier draft → out | `SanitizeModelResponse` | **Inspect and block** | Medium and above | A takedown notice must never carry leaked PII or a malicious URL |

A detection sets `injection_suspected` on the finding and surfaces a badge in the UI. This replaces
the hand-rolled regex canary previously planned — a first-party detector is better and more
defensible.

**Structural note:** even a *missed* injection cannot change a verdict, because the reconciler never
receives page text. Model Armor is defence in depth on top of an architecture that already contains
the failure — not the thing holding it back.

---

# 6. Observability

## 6.1 Two records, one story

The audit log and the trace describe the same events at different granularity. Do not build the
same thing twice.

| | Audit log (Firestore) | Trace (Cloud Trace) |
|---|---|---|
| Purpose | Business and legal defensibility | Operational debugging |
| Retention | Permanent | Sampled, short |
| Audience | A lawyer asking why a verdict was reached | An engineer asking why a sweep was slow |
| Sampling | **Never** — every decision | Sampled |

The UI decision-trail view reads the audit log. Each audit record carries its trace id, so one
pivots to the other.

## 6.2 Traces

One trace per sweep:

```
sweep
├── plan
├── discovery
│   ├── text_sweep → parallel_search (n spans)
│   └── image_sweep → vision_web_detection
├── triage[candidate]              ← one subtree per candidate
│   ├── fetch_page
│   ├── model_armor.sanitize_prompt
│   ├── extract
│   └── validate
├── reconcile
└── persist
```

Attributes on every agent span: `agent`, `prompt_version`, `cache_hit`, `cache_age_s`, `tokens_in`,
`tokens_out`, `retry_count`. On `reconcile`: `verdict`, `failing_check`, `matched_consent_id`.

So one trace answers *why did this finding get this verdict, and what did it cost* — the decision
trail and the performance profile become the same artifact.

Prefer ADK's and Agent Runtime's built-in OpenTelemetry instrumentation over hand-rolled spans;
verify how they export to Cloud Trace.

## 6.3 Logs

Structured JSON to Cloud Logging, one entry per harness step, correlated by trace id and sweep id.

**Never log page content or model output verbatim** — it is attacker-controlled and may contain PII.
Log hashes, lengths and classifications.

## 6.4 Metrics

Cloud Monitoring custom metrics.

**Operational**
```
sweep.duration_seconds                  histogram
sweep.candidates_discovered             counter
sweep.candidates_triaged                counter
tool.latency_seconds{tool}              histogram
tool.errors{tool, class}                counter     class = transient|permanent|semantic
circuit_breaker.opened{provider}        counter
cache.hit_ratio{tier}                   gauge       tier = content|ttl
tokens.used{agent, model}               counter
```

**Quality and security**
```
verdicts.total{verdict}                 counter     authorized|unauthorized|ambiguous
verdicts.ambiguous_ratio                gauge       a spike means threshold or model drift
extraction.validation_failures{reason}  counter     quote_not_verbatim|name_mismatch|bad_iso
model_armor.detections{type, path}      counter     injection|pii|malicious_url
evidence.snapshots_written              counter
assets.clearance_state{state}           gauge       unverified|cleared|blocked
```

`extraction.validation_failures{reason=quote_not_verbatim}` is the guardrail's own telemetry — a
non-zero rate is the model attempting to fabricate a citation and being caught. Alongside
`model_armor.detections{type=injection}`, it produces a dashboard that **demonstrates** the security
posture instead of asserting it. Worth four seconds of the demo video.

---

# 7. Evaluation

ADK ships an evaluation harness — use it rather than inventing one.
`adk eval <agent> <evalset>`, plus `AgentEvaluator` under pytest for CI.

| Evalset | Asserts | Metrics |
|---|---|---|
| `enforcement_happy` | A clean sweep calls `parallel_search` across the expected locales and produces cited findings | `tool_trajectory_avg_score`, `hallucinations_v1` |
| **`adversarial_injection`** | **Pages carrying injection attempts produce a verdict identical to the clean version** | `tool_trajectory_avg_score`, `safety_v1` |
| `verdict_matrix` | All seven rule branches plus territory scoping | exact match — no model call |
| `consent_extraction` | A contract PDF produces the expected grant, every field carrying a verbatim citation | `final_response_match_v2`, `hallucinations_v1` |
| `clearance_matrix` | cleared / blocked / unverified across the three seeded assets | exact match — no model call |

Build **`adversarial_injection` first** and show it on camera: a security regression test written in
the framework's own eval harness, asserting that a hostile page cannot move a verdict. Most
submissions will have no eval suite at all, let alone an adversarial one.

`verdict_matrix` and `clearance_matrix` are deterministic, so they are fast, stable and free to run
in CI on every commit.

---

# 8. What this changes in the build order

**WU-00 — the harness — comes before everything.** It is roughly four hours and it is the reason
the other ten agents are cheap. Then:

1. WU-00 harness (Prachit or Vedant, whoever starts first — it blocks both)
2. Firestore store + seed (Prachit) — simpler now, no SQLite
3. `parallel_search` + sweep (Vedant)
4. Triage + validators + Model Armor (Vedant)
5. Reconciler + `verdict_matrix` eval (Vedant)
6. UI (Prachit), clearance (Prachit)
7. Four runtime deployments + Cloud Run (both)
8. `adversarial_injection` evalset, dashboard, video

**Honest scope note.** This session added Model Armor, four runtimes with six service accounts, the
harness, full observability and five evalsets to an estimate that was already 64 hours. Realistically
that is +15 to +20 hours. The harness recovers a good part of it by making the per-agent work small,
and the deterministic evalsets are nearly free — but if Monday evening is behind, the order to cut
is: `ImageSweep` → scheduler → `enforcement_happy` and `consent_extraction` evalsets → the metrics
dashboard. **Never** cut `adversarial_injection`, the four deployments, or the video.
