# Hackathon resource → Consentinel component

Judging weights *"effective use of Google Cloud and Partner services"* at 25%. This maps every
resource from the hackathon resources page onto a component we actually run, so the Devpost writeup
can cite them and nothing sits unused by accident.

Source: https://agentic-cinema.devpost.com/resources

---

## Hard requirements, verbatim

**Overall build:** *"a functional, production-ready AI agent or multi-agent network—powered by
Gemini and Google Cloud Agent Builder."*

**Accepted SDKs:** *"google-adk, google-genai, google-generativeai, or google-cloud-aiplatform"*

**Runtime evidence:** the integration must be *"imported and actually called (a library import, an
app/backend entry point, or a loaded agent/flow/MCP config), not just named in the README."*

**Parallel track:** *"your project must actively use Parallel's Search API at runtime — for example,
via the official parallel-web SDK (Python or TypeScript), a supported integration such as the
Vercel AI SDK's @parallel-web/ai-sdk-tools or LangChain's ParallelWebSearchTool, or a Grounding
configuration using Parallel Web Search as the search provider. Referencing Parallel in your README
alone does not satisfy this requirement — the integration must be present in your code."*

**Platform:** must run on web, Android or iOS. No Google-exclusive hosting is mandated — Cloud Run
satisfies this.

**ADK install line (as published):**
```
pip install "google-cloud-aiplatform[agent_engines,adk]>=1.101.0"
```

---

## Mapping

### Phase 1 — Framework and environment

| Resource | Where we use it |
|---|---|
| Gemini Enterprise Agent Platform API setup | Project bootstrap; Gemini models for every extraction call |
| Gemini Enterprise Agent Platform SDK for Python (`googleapis/python-genai`) | `google-genai` client behind all model calls |
| Agent Engine — *Getting Started* notebook | Reference for hosting the two pipelines (see below) |
| Google Cloud free trial + hackathon credit form | **Action: submit the credit form ($100).** `forms.gle/XPe837tzogh8L5sX6` |
| Agent Builder guide / Dialogflow CX | Reviewed and not used — our flows are code-defined ADK pipelines, which is the sanctioned path. Note the reasoning in the writeup rather than leaving it unexplained |

### Phase 2 — Data processing and grounding

| Resource | Where we use it |
|---|---|
| **Document Processing guide** | **FR-1 consent ingestion** — contract PDF → structured permission grant. Use this approach over bare `pypdf` |
| RAG Q&A with BigQuery & PDFs | Optional stretch: natural-language querying across a registry of many contracts |
| Agent Builder Data Stores / Vertex AI Search | Optional stretch: index the contract corpus so clause lookup is grounded rather than re-extracted |
| **Gemini Multimodal use cases** | **FR-6.4 asset inspection** — recognisable performer, human voice present |
| **Video Transcription notebook** | **FR-6** clearance on video/audio deliverables; transcript is content-addressed and cached (TECHNICAL_DESIGN §6.1) |
| **Video Captioning notebook** | **FR-6** shot-level description for the clearance record |
| **Imagen 3** | **Generate the fictional performer's reference images** for the reverse-image sweep (FR-2.4). Also satisfies LE-1 — we need reference photos and must not use a real person's |
| **Lyria 3 / Gemini Flash TTS** | **Generate the fake "cloned voice" clip** that the clearance demo checks (FR-6). Gives us realistic demo media with no real performer involved |
| Multi-speaker podcast generator | Not used |
| Multimodal sentiment analysis | Not used |

> The genmedia tools are not the product, but they are the honest way to build our demo fixtures.
> That is worth stating explicitly: we generate the synthetic media *we* test against, so no real
> person's voice or face appears anywhere in the submission.

### Phase 3 — Partner integration

| Resource | Where we use it |
|---|---|
| **Parallel resources** | **FR-2.3 discovery**, via the official `parallel-web` Python SDK in `consentinel/tools/parallel_search.py`. Every call logged with timestamp, query, locale and result count |
| Parallel **Extract API** | **OQ-5** — if it returns full page content, adopt it in place of our own `fetch_page`, so the partner service covers discovery *and* retrieval at runtime |
| IBM / Grafana / ClickHouse / Replit | Other tracks. ClickHouse remains the documented scale path for `findings` + `audit_log` (TECHNICAL_DESIGN §5.4) — subject to OQ-2 |

### Phase 4 — Reasoning, state, hosting

| Resource | Where we use it |
|---|---|
| **ADK install line** | Pinned in `requirements.txt` |
| **Deploying ADK Agents to Agent Engine** | **Host both pipelines on Agent Engine**; the FastAPI UI on Cloud Run calls them. This is what makes "powered by Gemini and Google Cloud Agent Builder" true in the code rather than in the README |
| Introduction to Agent Engine | Reference |
| **Introduction to Function Calling** | Our tool layer: `parallel_search`, `fetch_page`, `vision_web_detection` |
| **Forced Function Calling** | Guarantees the extractor emits a `TriageExtraction` instead of prose — directly implements guardrail L1 |
| Multimodal Function Calling | FR-6.4 escalation path |
| MCP Database Toolbox | Only if we adopt ClickHouse (OQ-2) |
| Live API on Agent Engine | Not used |
| Google Maps agent tutorial | Not used |

### Phase 5 — Deployment and safety

| Resource | Where we use it |
|---|---|
| **Cloud Run quickstart** | Hosted URL for the submission (NFR-3). Deploy on 8 Sep, not the 9th |
| **Secret Manager** | Production secrets — replaces `.env` in deploy and satisfies NFR-4 on a public repo |
| **Gemini safety settings** | Configured on generation calls; documented alongside guardrails (TECHNICAL_DESIGN §3) |
| Agent Builder deployment guide | Reference for the Agent Engine deploy |

---

## Two decisions this changes

1. **Deploy the agents to Agent Engine**, with Cloud Run serving only the UI. The brief says
   "powered by Gemini and Google Cloud Agent Builder", and Agent Engine is the sanctioned runtime for
   ADK agents. Raw Gemini API calls from a Cloud Run container would technically satisfy the SDK list
   but weakly satisfy the framing.

2. **Use Imagen 3 and TTS to manufacture our own demo media.** We need reference images for the
   reverse-image sweep and a fake cloned-voice clip for the clearance check. Generating both means
   the submission demonstrates the platform's generative tools *and* contains no real person's
   likeness — LE-1 satisfied by construction rather than by disclaimer.

## Open actions

| ID | Action | Owner |
|---|---|---|
| RA-1 | Submit the Google Cloud credit form (`forms.gle/XPe837tzogh8L5sX6`) | either, today |
| RA-2 | Confirm Parallel Search locale/region parameters in the full API reference (OQ-1) | A |
| RA-3 | Confirm whether Extract API returns full page content (OQ-5) | A |
| RA-4 | Stand up an Agent Engine deployment early — do not discover this on 9 Sep | A |
| RA-5 | Generate reference images (Imagen 3) + cloned-voice clip (TTS) for fixtures | B |
