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

No Google-exclusive hosting is mandated. **Cloud Run satisfies this.** We additionally deploy the
agents to **Agent Engine** so that "powered by Gemini and Google Cloud Agent Builder" is true in
code, not just in prose — see `RESOURCE_MAP.md`.

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

- **Maximum 4 people per team.** We are 2
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
| Hosted URL broken on submission day | Deploy 8 Sep, not 9 Sep. Test from a cold browser with no local server running |
| Missing or incomplete license | `LICENSE` (MIT) committed at the repo root |
| Video over 3 minutes, or private | Script it, time it, set visibility to public — not unlisted-only if the form requires public |
| Secret committed to the public repo | `.env` gitignored; Secret Manager in deploy; rotate on any exposure |
| Submitted late | Target 12:00 PDT on 9 Sep |
