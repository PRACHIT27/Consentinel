# Consentinel — architecture diagrams

Mermaid sources. These render natively in **Notion** (`/code` block → language `Mermaid`), in
GitHub, and in most wikis. Paste the fenced block contents, not the fence.

For the demo video, diagram 5 (trust boundary) is the most distinctive — most submissions won't
have one.

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
        E1["QueryPlanner<br/>queries x modality x locale"]
        E2["TextSweep<br/>parallel_search"]
        E3["ImageSweep<br/>vision_web_detection"]
        E4["Triage<br/>fetch + structured extraction"]
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
    QP->>QP: build queries x modality x locale
    loop per query
        QP->>PS: search(query, locale)
        PS-->>QP: url, title, excerpts
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

    W -->|"content as DATA<br/>delimited field"| F
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
