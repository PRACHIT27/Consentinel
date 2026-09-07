# Consentinel — architecture diagrams

**Status: 🔒 FROZEN at v1.0.0 (2026-09-07)** — approved by Prachit; Vedant and Swara to acknowledge.
Safe to design against. Structural changes from here need all three to agree and a MAJOR bump in
[VERSION.md](VERSION.md).

Runtime placement, IAM, the agent harness, Model Armor, observability and evaluation are in
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md). Diagrams 8 and 9 below cover deployment and the harness.

Mermaid sources. These render natively in **Notion** (`/code` block → language `Mermaid`), in
GitHub, and in most wikis. Paste the fenced block contents, not the fence.

For the demo video, diagram 5 (trust boundary) is the most distinctive — most submissions won't
have one.

## What the Parallel API research settled

Both blocking questions are answered (confirmed against docs.parallel.ai, 7 Sep 2026):

- **Locale is natively supported.** `location` takes an ISO 3166-1 alpha-2 country code, and queries
  may be written in any language with no extra configuration — 26+ languages, 30+ countries. Our
  `Locale(language, region)` maps directly: region → `location`, language → the language the query
  is written in.
- **Extract does *not* return full page content.** It returns compressed, objective-scoped excerpts.
  So it **cannot** replace `fetch_page` and **cannot** serve as evidence. `fetch_page` stays.
- Extract earns a place as an **optional cheap first-pass read** during triage, with `fetch_page`
  reserved for candidates escalating to a dossier. Fewer full fetches, more of the judged partner
  service at runtime. Marked P1 — if it's cut, nothing else changes.
- **No second partner product.** We are not adopting ClickHouse for v1, so OQ-2 no longer gates
  anything. SQLite behind the `Store` interface; ClickHouse stays a documented post-hackathon path.
- One contract consequence: `search_queries` takes **2–3 queries of 3–6 words each** per call, with
  an `objective`. `QueryPlanner` batches accordingly. See `tools/contracts.py`.

---

## 1. System context

Who talks to what.

```mermaid
flowchart LR
    A["Rights protector<br/>agency, estate"]
    B["Delivery gatekeeper<br/>studio business affairs"]
    C["Consentinel"]
    D["Parallel Search API"]
    E["Gemini on Vertex AI"]
    F["Cloud Vision<br/>web detection"]
    G["Open web<br/>untrusted"]
    H["Google Cloud Storage<br/>evidence"]

    A -->|"registers grants, runs sweeps"| C
    B -->|"submits deliverables"| C
    C -->|"discovery"| D
    C -->|"extraction, inspection"| E
    C -->|"reverse image"| F
    C -->|"fetch and snapshot"| G
    C -->|"immutable evidence"| H
    C -->|"case files, blockers list"| A
    C -->|"clearance manifest"| B
```

---

## 2. Component architecture

The registry is the spine; two pipelines read from it in opposite directions and share one rule
engine.

```mermaid
flowchart TB
    subgraph REG["Consent registry — the spine"]
        R1[("performers")]
        R2[("consents")]
    end

    subgraph ENF["Enforcement pipeline — outward"]
        E1["QueryPlanner<br/>2-3 queries x 3-6 words<br/>x modality x locale"]
        E2["TextSweep<br/>parallel_search<br/>location = ISO alpha-2"]
        E3["ImageSweep<br/>vision_web_detection<br/>P2, cut first"]
        E4["Triage<br/>parallel_extract first pass P1<br/>then structured extraction"]
        E6["fetch_page<br/>full content, on escalation"]
        E5["DossierWriter<br/>evidence + draft notice"]
    end

    subgraph RULE["Shared rule engine"]
        RE["Reconciler<br/>deterministic rules<br/>no model involved"]
    end

    subgraph CLR["Clearance pipeline — inward"]
        C1["AssetIngest<br/>hash + provenance metadata"]
        C2["PaperworkParser<br/>vendor invoice"]
        C3["AssetInspector<br/>Gemini multimodal"]
        C4["ManifestBuilder<br/>rollup + blockers"]
    end

    subgraph STORE["Storage"]
        S1[("findings")]
        S2[("assets")]
        S3[("dossiers")]
        S4[("audit_log<br/>append only")]
        S5["GCS evidence<br/>immutable, no TTL"]
    end

    REG --> E1
    E1 --> E2
    E1 --> E3
    E2 --> E4
    E3 --> E4
    E4 --> RE
    E4 -.->|"escalating to dossier"| E6
    E6 --> E5
    C1 --> C2
    C2 --> C3
    C3 --> RE
    REG --> RE
    RE --> S1
    RE --> S2
    RE --> C4
    S1 --> E5
    E5 --> S3
    E5 --> S5
    ENF --> S4
    CLR --> S4
    RULE --> S4
```

---

## 3. Enforcement sequence

```mermaid
sequenceDiagram
    actor U as Rights protector
    participant QP as QueryPlanner
    participant PS as Parallel Search
    participant TR as Triage
    participant W as Web page (untrusted)
    participant RC as Reconciler
    participant DB as Store + audit_log

    U->>QP: run sweep for performer
    QP->>QP: build 2-3 queries x 3-6 words, per modality and locale
    loop per batch
        QP->>PS: search(objective, search_queries[2-3], location=ISO, mode=basic)
        PS-->>QP: url, title, publish_date, excerpts
    end
    QP->>QP: dedupe on url_hash
    loop per candidate
        TR->>W: fetch page
        W-->>TR: page text (treated as data)
        TR->>TR: structured extraction + validators
        Note over TR: verbatim quote check<br/>ISO codes, name match
        TR->>RC: TriageExtraction only
        Note over RC: raw page text never<br/>reaches this step
        RC->>DB: read consents
        RC->>RC: evaluate rules in order
        RC->>DB: finding + verdict + citation
        RC->>DB: audit event with cache age
    end
    DB-->>U: findings queue, colour coded
```

---

## 4. Clearance sequence

```mermaid
sequenceDiagram
    actor S as Delivery gatekeeper
    participant AI as AssetIngest
    participant PP as PaperworkParser
    participant AS as AssetInspector
    participant RC as Reconciler
    participant DB as Store

    S->>AI: upload deliverable (+ vendor invoice)
    AI->>AI: content hash, read provenance metadata
    Note over AI: missing metadata is itself<br/>a recorded signal
    AI->>PP: vendor paperwork
    PP-->>AI: declared generative usage
    AI->>AS: low bitrate proxy
    AS-->>AI: performer present? human voice?
    AI->>RC: structured observations
    RC->>DB: read consents
    RC->>RC: same rule engine as enforcement
    alt grant covers this use
        RC->>DB: cleared + matched grant
    else grant exists but does not cover
        RC->>DB: blocked + failing check
    else no paperwork at all
        RC->>DB: unverified (default)
    end
    DB-->>S: blockers list
```

---

## 5. Trust boundary — the distinctive one

Where untrusted content enters, and where it is structurally prevented from reaching a decision.

```mermaid
flowchart TB
    subgraph UNTRUSTED["UNTRUSTED ZONE"]
        W["Third party web page<br/>may contain text addressed to the agent"]
    end

    subgraph BOUNDARY["BOUNDARY — extraction only"]
        X["parallel_extract<br/>compressed excerpts, P1<br/>still untrusted content"]
        F["fetch_page<br/>SSRF guards, size caps, no credentials"]
        T["Triage extractor<br/>NO TOOLS<br/>schema constrained output only"]
        V["Validators<br/>quote must be verbatim substring<br/>name must match registry<br/>ISO codes, confidence range"]
    end

    subgraph TRUSTED["TRUSTED ZONE — no page text can reach here"]
        RC["Reconciler<br/>deterministic rules"]
        REG[("Consent registry")]
        DEC["Verdict + citation"]
    end

    subgraph HUMAN["HUMAN ONLY"]
        H["Send takedown notice<br/>no send capability exists in the product"]
    end

    W -->|"content as DATA<br/>delimited field"| X
    W -->|"content as DATA<br/>delimited field"| F
    X --> T
    F --> T
    T -->|"TriageExtraction"| V
    V -->|"validated fields only"| RC
    REG --> RC
    RC --> DEC
    DEC -->|"drafted, never sent"| H

    W -.->|"BLOCKED: injected instructions<br/>cannot express themselves<br/>through the schema"| RC
```

---

## 6. Data model

```mermaid
erDiagram
    PERFORMERS ||--o{ CONSENTS : "granted for"
    PERFORMERS ||--o{ FINDINGS : "suspected use of"
    PERFORMERS ||--o{ ASSETS : "appears in"
    CONSENTS ||--o{ FINDINGS : "matched by"
    CONSENTS ||--o{ ASSETS : "covers"
    FINDINGS ||--o| DOSSIERS : "escalates to"

    PERFORMERS {
        text id PK
        text name
        json aliases
        json reference_images
    }
    CONSENTS {
        text id PK
        text performer_id FK
        text licensee
        json permitted_uses
        json territories
        date valid_from
        date valid_to
        json clause_citations
    }
    FINDINGS {
        text id PK
        text url
        text url_hash UK
        text discovered_locale
        json target_territories
        text modality
        bool is_commercial
        text evidence_quote
        real confidence
        text verdict
        text matched_consent_id FK
        text status
        text evidence_uri
    }
    ASSETS {
        text id PK
        text production_id
        text shot_code
        json provenance_metadata
        text vendor
        text synthetic
        text clearance_state
        text matched_consent_id FK
    }
    DOSSIERS {
        text id PK
        text finding_id FK
        json evidence_bundle
        text draft_notice
    }
    AUDIT_LOG {
        text id PK
        text ts
        text actor
        text subject_id
        json tool_calls
        text prompt_version
    }
```

---

## 7. Verdict rule flow

The decision logic, as deterministic code. Worth putting in Notion so everyone agrees on it before
it is implemented.

```mermaid
flowchart TD
    A["Observation:<br/>performer, modality, territories,<br/>actor, confidence"] --> B{"Grant exists<br/>for performer?"}
    B -->|no| X["UNAUTHORIZED<br/>no grant on file"]
    B -->|yes| C{"Grant covers<br/>this modality?"}
    C -->|no| X2["UNAUTHORIZED<br/>modality not permitted"]
    C -->|yes| D{"All target territories<br/>within grant?"}
    D -->|no| X3["UNAUTHORIZED<br/>outside territory"]
    D -->|yes| E{"Today within<br/>validity window?"}
    E -->|no| X4["UNAUTHORIZED<br/>expired or not yet effective"]
    E -->|yes| F{"Actor equals<br/>licensee?"}
    F -->|no| X5["UNAUTHORIZED<br/>third party, not the licensee"]
    F -->|yes| G{"Confidence<br/>>= 0.6?"}
    G -->|no| Y["AMBIGUOUS<br/>human review queue"]
    G -->|yes| Z["AUTHORIZED<br/>cite matched clause"]
```

Note: any failure or exception anywhere in the pipeline resolves to **AMBIGUOUS** or **UNVERIFIED**,
never to AUTHORIZED or CLEARED. See `TECHNICAL_DESIGN.md` §0.

---

## 8. Deployment and trust zones

Four Agent Runtime deployments, six service accounts. Full IAM table in
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) §3.

```mermaid
flowchart TB
    U["Users and judges"]

    subgraph CR["Cloud Run — consentinel-web@"]
        W["FastAPI + Jinja<br/>Registry · Findings · Clearance"]
    end

    subgraph AR["Agent Runtime — 4 deployments"]
        E["cn-enforcement<br/>consentinel-enforcement@<br/>Planner · Sweep · Reconciler · Dossier"]
        T["cn-triage — ISOLATED<br/>consentinel-triage@<br/>Triage only · no tools · no writes"]
        C["cn-clearance<br/>consentinel-clearance@<br/>Ingest · Paperwork · Inspector · Manifest"]
        I["cn-ingest<br/>consentinel-ingest@<br/>ConsentIngest"]
    end

    subgraph DATA["Data"]
        FS[("Firestore<br/>registry · findings · assets · audit")]
        EV[("GCS evidence<br/>retention lock<br/>no delete path")]
        UP[("GCS uploads and derived")]
        SM["Secret Manager<br/>Parallel key"]
    end

    subgraph EXT["External"]
        P["Parallel Search"]
        G["Gemini on Vertex"]
        MA["Model Armor"]
        V["Cloud Vision"]
        WEB["Open web — UNTRUSTED"]
    end

    SCH["Cloud Scheduler<br/>consentinel-scheduler@"]

    U --> W
    W -->|invoke| E
    W -->|invoke| C
    W -->|invoke| I
    E -->|invoke| T
    SCH -->|"run.invoker only"| W

    E --> P
    E --> V
    E --> G
    T --> G
    T --> MA
    C --> G
    I --> G
    WEB -.->|"hostile content"| T

    E -->|"objectCreator<br/>create only"| EV
    W -->|"objectViewer<br/>read only"| EV
    C --> UP
    I --> UP
    E --> FS
    C --> FS
    I --> FS
    T -->|"read only"| FS
    E --> SM
```

**The property to notice:** no principal holds `objectAdmin` on the evidence bucket. Combined with
retention lock and object versioning, **nothing in the system can delete evidence.** And Triage —
the only component touching attacker-controlled content — holds the weakest permissions of anything
here: no secrets, no storage, no database writes, no tools.

---

## 9. The agent harness

Every agent runs through one wrapper. Built once (WU-00), it is why the other ten agents are cheap.

```mermaid
flowchart LR
    IN["Agent invocation"] --> S1["1 open trace span"]
    S1 --> S2["2 budget check<br/>timeout, token ceiling"]
    S2 --> S3["3 cache lookup<br/>records cache_age_s"]
    S3 --> S4["4 Model Armor<br/>SanitizeUserPrompt<br/>untrusted input only"]
    S4 --> S5["5 invoke<br/>temp 0, forced function calling"]
    S5 --> S6["6 Model Armor<br/>SanitizeModelResponse<br/>outward text only"]
    S6 --> S7["7 validate schema<br/>+ field validators"]
    S7 -->|"invalid"| S8["8 repair — one attempt"]
    S8 --> S7
    S7 -->|"valid"| S11["11 audit append"]
    S5 -->|"error"| S9["9 classify, retry,<br/>circuit break"]
    S9 -->|"unrecovered"| S10["10 FAIL SAFE<br/>ambiguous / unverified / degraded<br/>NEVER authorized or cleared"]
    S10 --> S11
    S11 --> S12["12 emit metrics,<br/>close span"]
    S12 --> OUT["Result"]
```

Each agent declares a `HarnessPolicy` carrying its timeout, retry budget, output schema, cache
regime, Model Armor templates and **`tools` tuple**. That tuple is not documentation — the harness
refuses any tool call not named in it, which is how the trust boundary is enforced in code rather
than by convention. Triage declares `tools=()`.
