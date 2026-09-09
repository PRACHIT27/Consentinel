# Competition requirements — Agentic Cinema

Everything we are being judged against. Requirements that could disqualify us are quoted verbatim.

**Event:** Agentic Cinema: The Blockbuster Hackathon (Devpost, run by Google Cloud)
**Our track:** **Parallel**
**Links:** [main](https://agentic-cinema.devpost.com/) · [rules](https://agentic-cinema.devpost.com/rules) ·
[resources](https://agentic-cinema.devpost.com/resources) ·
[forum](https://agentic-cinema.devpost.com/forum_topics) · [Discord](https://discord.gg/7Dqk5ebCD4)

---

## 1. Deadline

**9 September 2026, 2:00 PM PDT.**

We submit by **12:00 PDT** — two hours of buffer. Late is worth zero regardless of quality.

## 2. What we must build

> *"Build a functional agent—powered by Gemini and Google Cloud Agent Builder—that integrates a
> Partner Entity's product or MCP to power a real media & entertainment workflow."*

And from the resources page:

> *"a functional, production-ready AI agent or multi-agent network—powered by Gemini and Google
> Cloud Agent Builder."*

Note **"production-ready"** and **"real workflow"**. A proof of concept scores badly on Design.

## 3. Required technology

**Accepted Google SDKs — verbatim:**

> *"Accepted Google Cloud packages/SDKs: google-adk, google-genai, google-generativeai, or
> google-cloud-aiplatform"*

**ADK install line, as published on the resources page:**

```bash
pip install "google-cloud-aiplatform[agent_engines,adk]>=1.101.0"
```

**Build natively.** The resources page directs building *"natively using the Agent Development Kit
(ADK) instead of external wrapper libraries."*

### 3.1 No other AI models — read this carefully

**No AI models or APIs from AWS, Microsoft, OpenAI or Anthropic may be used in the project.**

This does **not** stop us using Claude Code as our coding assistant — that is a development tool,
like an IDE. What it means is that **the shipped agent must run on Gemini**: every model call in
`consentinel/` goes to Gemini via an accepted Google SDK. No OpenAI or Anthropic client libraries in
`requirements.txt`, no fallback to a non-Google model anywhere in the pipeline.

## 4. Evidence of runtime use — the disqualification risk

> *"imported and actually called (a library import, an app/backend entry point, or a loaded
> agent/flow/MCP config), not just named in the README."*

**Parallel track requirement, verbatim:**

> *"your project must actively use Parallel's Search API at runtime — for example, via the official
> parallel-web SDK (Python or TypeScript), a supported integration such as the Vercel AI SDK's
> @parallel-web/ai-sdk-tools or LangChain's ParallelWebSearchTool, or a Grounding configuration
> using Parallel Web Search as the search provider. Referencing Parallel in your README alone does
> not satisfy this requirement — the integration must be present in your code."*

**How we satisfy it:** `consentinel/tools/parallel_search.py` imports the official `parallel-web`
Python SDK and calls it from the discovery pipeline. Every call is logged with timestamp, query,
locale and result count, and that log is shown on screen in the demo video.

**Do not** replace the SDK with raw `httpx` calls to their endpoint, even if it seems simpler.

## 5. Platform

> Projects must run on *"at least one of the following platforms: web, Android, or iOS"*

No Google-exclusive hosting is mandated. **Cloud Run satisfies this**, and Cloud Run is where the
app runs: https://consentinel-web-255860737849.us-central1.run.app

**WU-24 is done, so Agent Engine can be claimed — in these words.** Four runtimes are deployed
(`cn-ingest`, `cn-triage`, `cn-clearance`, `cn-enforcement`), each on its own service account, each
hosting its pipeline's model step. Resource names are in `infra/agent_engine/deployed.json` and the
answers they gave are in `infra/agent_engine/smoke_output.json`.

What to say: *"the agents are built with ADK and deployed to Vertex AI Agent Engine; the app runs on
Cloud Run, and the deterministic rule engine that decides every verdict runs in code next to the
registry."* All of that is checkable.

What **not** to say: that the whole pipeline executes on Agent Engine. It does not — the app calls
the same agents in-process, because the reconciler reads the registry and has no model. Overstating
this is the kind of claim a judge can puncture in one question.

## 6. Submission checklist

Every item is required. Missing one is fatal.

- [ ] **Hosted project URL** — must work from a cold start, no local dependencies
- [ ] **Demo video, max 3 minutes** — YouTube or Vimeo, English or subtitled, publicly visible
- [ ] **Public code repository** — GitHub/GitLab/Bitbucket, open source, **with a complete license**
      (we ship MIT in `LICENSE`)
- [ ] **Partner track selected** — Parallel
- [ ] **Devpost submission form completed**
- [ ] **Evidence of runtime use** of Google Cloud and Parallel (screenshots + the call log)

## 7. Judging criteria — 25% each

| Criterion | What it means | Where we win it |
|---|---|---|
| **Technological Implementation** | Effective use of Google Cloud and Partner services | ADK-native pipelines on Agent Engine; Parallel load-bearing in discovery; Gemini multimodal in clearance |
| **Design** | A complete, coherent product experience beyond a proof of concept | Three real views ending in an artefact — a case file, a blockers list — not a chat box |
| **Potential Impact** | A credible solution to a real problem for a real audience | Named buyers (agencies, estates, studio business affairs); real litigation as precedent (`PRECEDENT` section of the writeup) |
| **Quality of Idea** | Creative application of Google Cloud and Partner tools | Territory-scoped verdicts; one rule engine serving enforcement and clearance; injection-resistant design |

## 8. Team and eligibility

- **Maximum 4 people per team.** We are 3
- One representative must be authorised to submit
- Participants must be of legal majority age in their country of residence
- Roughly 24 countries and territories are excluded (including Afghanistan, China, Russia, Cuba,
  Iran and others). Confirm your own eligibility on the rules page before investing the weekend

## 9. Prizes

$75,000 total across five partner tracks. Within our track: **1st $7,500 · 2nd $4,500 · 3rd $3,000.**
Five separate podiums, so track choice materially affects odds.

## 10. Credits and forms

| Form | Purpose |
|---|---|
| `forms.gle/XPe837tzogh8L5sX6` | **Google Cloud hackathon credits ($100). Submit this now — approval is not instant** |
| `forms.gle/pwwvgDvbkgiRpADm6` | Replit credits — not our track, ignore |

## 11. Things that would sink us

| Risk | Guard |
|---|---|
| Parallel only mentioned, not called | Official SDK imported and called in `tools/parallel_search.py`; call log on camera |
| A non-Google model in the pipeline | Nothing but Gemini via accepted SDKs. Check `requirements.txt` before submitting |
| Hosted URL broken on submission day | **Live: https://consentinel-web-255860737849.us-central1.run.app** — Cloud Run, `us-central1`, public, no login. Re-tested from a cold browser after the 9 Sep UI deploy |
| Missing or incomplete license | `LICENSE` (MIT) committed at the repo root |
| Video over 3 minutes, or private | Script it, time it, set visibility to public — not unlisted-only if the form requires public |
| Secret committed to the public repo | `.env` gitignored; Secret Manager in deploy; rotate on any exposure |
| Submitted late | Target 12:00 PDT on 9 Sep |


---

# 12. Hackathon resources mapped to components

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
| **Video Transcription notebook** | **FR-6** clearance on video/audio deliverables; transcript is content-addressed and cached (DESIGN Part I §6.1) |
| **Video Captioning notebook** | **FR-6** shot-level description for the clearance record |
| ~~**Imagen 3**~~ → **Gemini image generation** | **Generate the fictional performer's reference images** for the reverse-image sweep (FR-2.4). Also satisfies LE-1 — we need reference photos and must not use a real person's. **Imagen is not enabled on project `consentinel`** (every `imagen-*` id 404s), so `tools/make_demo_media.py` uses `gemini-3-pro-image` on the `global` endpoint. ✅ done |
| **Gemini Flash TTS** | **Generate the fake "cloned voice" clip** that the clearance demo checks (FR-6). Gives us realistic demo media with no real performer involved. `gemini-2.5-flash-tts`, 6.9s of 24 kHz mono. ✅ done |
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
| IBM / Grafana / ClickHouse / Replit | Other tracks. ClickHouse remains the documented scale path for `findings` + `audit_log` (DESIGN Part I §5.4) — subject to OQ-2 |

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
| **Gemini safety settings** | Configured on generation calls; documented alongside guardrails (DESIGN Part I §3) |
| Agent Builder deployment guide | Reference for the Agent Engine deploy |

---

## Two decisions this changes

1. **Deploy the agents to Agent Engine**, with Cloud Run serving only the UI. The brief says
   "powered by Gemini and Google Cloud Agent Builder", and Agent Engine is the sanctioned runtime for
   ADK agents. Raw Gemini API calls from a Cloud Run container would technically satisfy the SDK list
   but weakly satisfy the framing.

2. **Use Gemini image generation and TTS to manufacture our own demo media.** We need reference images for the
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
| RA-5 | ✅ done — reference image (`gemini-3-pro-image`) + cloned-voice clip (`gemini-2.5-flash-tts`) in `fixtures/media/`, regenerate with `python tools/make_demo_media.py --force` | B |
