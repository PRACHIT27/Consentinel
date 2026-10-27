# Devpost submission — paste-ready

Each `##` heading below is one field of the Devpost form, in the form's order. Everything here is
checked against the code on `main` (see the last section for where each claim is backed). If you
edit, **do not add** any of the claims listed under "Do not say".

---

## Project name

Consentinel

## Tagline (one line)

A permission-slip registry for AI copies of performers: find who's using an actor's face or voice
without consent, and stop a studio shipping what it never had the rights to.

## Track

**Parallel**

## Links

- **Hosted app:** https://consentinel-web-255860737849.us-central1.run.app — start at `/try`
- **Code:** https://github.com/PRACHIT27/Consentinel (MIT)
- **Video:** <YouTube/Vimeo URL — public, under 3:00>

---

## Inspiration

AI can now copy an actor's face and voice, so performer contracts have started spelling out exactly
what may be done with that copy: *your voice, for this film, in the US and Canada, for three years;
never a synthetic version of your face.* That is a permission slip, and nobody keeps track of them.

Two things go wrong. **Outward:** websites openly sell "AI voice of [actor]", and the agency finds
out by accident, months later. **Inward:** a film is assembled by dozens of vendors, any of whom may
have used a generative tool, and before release someone has to sign that the production holds the
rights to everything in it. Today they establish that by emailing vendors and hoping.

When SAG-AFTRA challenged the AI Darth Vader voice in *Fortnite*, the striking detail was that James
Earl Jones *had* signed a grant — the fight was over whether it covered that use. That is exactly
the question we built Consentinel to answer: **does a permission slip exist for this use, in this
territory, on this date, for this licensee?**

## What it does

One consent registry, read in two opposite directions by the same rule engine.

1. **Add a permission slip.** Drop in a performer agreement (PDF). Gemini extracts the performer,
   licensee, permitted uses, territories and validity window, and quotes the source sentence for
   each. A field whose quote is not literally on the page is dropped, not guessed. It catches the
   hard cases too — a grant whose term has already expired, and a "shall not generate a synthetic
   visual likeness" clause buried inside a paragraph of things the producer *may* do.
2. **Sweep the web (enforcement).** Gemini plans search phrases in five languages (English,
   Portuguese, Spanish, Japanese, Hindi) for five markets, and **Parallel's Search API** runs them
   with a country `location` for each. Candidate pages are checked against Google Web Risk, fetched,
   and read.
3. **Judge.** Two separate steps, and the separation is the point. Gemini *reads* each page and fills
   a fixed schema — nothing else. Then **plain code, no model,** checks those answers against the
   registry and returns `authorized`, `unauthorized` or `ambiguous`.
4. **Build the case file.** For an unauthorised finding: an immutable snapshot of the page, the
   exact clause it breaches, and a draft takedown notice. **There is no send button, by design** — a
   person sends it.
5. **Check your own delivery (clearance).** Upload a clip — audio, video or image — and the *same*
   rules answer *cleared*, *blocked* or *unverified*. Our sample voice clip clears for the US, and the
   same file is blocked for Brazil because the grant covers the US and Canada only. Every asset
   starts `unverified`; only a covering grant clears it.

Steps 1–4 catch outsiders. Step 5 catches yourself.

## How we built it

- **Gemini on Vertex AI** (`google-genai`, `gemini-2.5-flash`, temperature 0, schema-constrained
  output) for contract extraction, query planning, page reading, multimodal clip inspection and
  the draft notice.
- **Google ADK** for every agent, chained in fixed code order rather than by LLM routing — the
  model never chooses the next step. The agents are **deployed to Vertex AI Agent Engine as four
  runtimes** — `cn-ingest`, `cn-triage`, `cn-clearance`, `cn-enforcement` — each on its own service
  account. The app itself runs on **Cloud Run**, and the deterministic rule engine that decides every
  verdict runs in code next to the registry.
- **Parallel Search** via the official `parallel-web` SDK (`client.search`) is load-bearing in
  discovery: 2–3 short keyword queries plus a natural-language objective per call, with the market
  passed as `location` in `advanced_settings`. The repo carries a redacted log of 8 live calls with
  Parallel's own `search_id`s (`evidence/published/`).
- **Firestore** holds the registry; **Cloud Storage** holds evidence snapshots in a bucket where no
  principal in the system can delete; **Model Armor** screens model inputs for injection and PII and
  screens the draft notice on the way out; **Web Risk** vets every address before we open it;
  **Secret Manager** holds the keys; **Cloud Trace** via OpenTelemetry traces each request.
- A shared agent **harness** gives every step retries, a cache keyed on prompt version and locale,
  an audit-log line per call, and a fail-safe: any error resolves to `ambiguous`, `unverified` or
  `degraded`, never to `authorized` or `cleared`. A crash is always distinguishable from a clean
  result.
- `DEMO_MODE` replays the whole pipeline from cache with zero external calls.
- FastAPI + Jinja front end. 645 tests, plus `adversarial_injection` and `verdict_matrix` eval sets
  running in CI.

## Challenges we ran into

- **Web pages talk back.** The pages we read are written by the people we are investigating, and
  some will address our agent directly. We made that structurally harmless rather than filtered:
  the page-reading agent has no tools, no secrets and no write access, and with a response schema
  set, ADK refuses tools and agent transfer outright. The reconciler that decides verdicts never
  sees page text at all. Our own planted page, `/demo/listing-injected`, tells the agent to report
  the listing as authorised. The verdict does not move.
- **Hallucinated citations.** A case file is useless if the quoted clause was invented. Every
  `evidence_quote` must be a verbatim substring of the source, or the extraction is rejected. One
  string comparison makes quote hallucination structurally impossible.
- **Real companies in a public demo.** A live sweep for our invented performer returned real
  third-party sites. We count and log them, and **withhold** them: a public demo does not publish an
  authorisation verdict naming a real company. The finding you see is a page we host ourselves.
- **Agent Engine deploys.** Four things fail in ways that name something other than the cause:
  reserved env var names, relative-path packaging, session permissions on the runtime's own service
  account, and an ADK version mismatch with the serving container. All are written up in
  `DESIGN.md` §2.0 so the next person doesn't lose an evening to them.

## Accomplishments that we're proud of

- **The model never decides a verdict.** Gemini reads; code decides. No prompt change can turn a
  blocked clip into a cleared one, because clearance comes from a contract record the model never
  sees.
- **One rule engine, two directions.** A studio gets the same answer about its own footage as it
  would about a stranger's website. That equivalence is the product.
- **Territory- and date-scoped answers**, not a yes/no on "is this AI": the same voice is cleared
  in the US and blocked in Brazil.
- **No outward action is ever automatic.** Notices are drafted; humans send them. There is no send
  capability anywhere in the code.
- **No real person's likeness anywhere.** Our performer, *Mira Vance*, is fictional, and her face and
  cloned-voice sample were generated by us with Gemini image generation and Gemini TTS.

## What we learned

- Separating "read" from "decide" is what makes an agent trustworthy in a legal workflow. Once the
  model can only fill fields, prompt injection has nowhere to go.
- Failing toward doubt is a design decision you make once, in the harness, or you make it wrong six
  times in six agents.
- Parallel's `location` plus queries written natively in each language finds listings an
  English-only sweep never would. The market is where the territory clause bites.

## What's next for Consentinel

- **Media sweeps.** Discovery is text-only today. Voice cloning is sold as audio samples and
  synthetic endorsements run as video ads, so text search finds the listing without confirming the
  offering. Audio, video and reverse-image sweeps are designed (`DESIGN.md` §2.2) and drafted in
  `consentinel/agents/media_sweep.py`, but not yet wired into the product.
- **Route the app through Agent Engine.** The runtimes are deployed and answering; the remaining hop
  is the app calling them instead of running the same agents in-process.
- **Content Credentials (C2PA) and invoice/SOW parsing** in clearance, so a delivery manifest clears
  itself instead of relying on declared facts.
- **Scheduled sweeps** and a distributor-facing clearance report, the document a buyer demands when
  acquiring a finished film.

## Built with

`gemini` · `vertex-ai` · `google-adk` · `vertex-ai-agent-engine` · `google-genai` · `parallel-web` ·
`parallel-search` · `cloud-run` · `firestore` · `cloud-storage` · `model-armor` · `web-risk` ·
`secret-manager` · `cloud-trace` · `opentelemetry` · `python` · `fastapi` · `jinja`

---

## Testing instructions for judges

> **Put this in the form's private "testing instructions / additional info for judges" field, not
> the public description.** It carries the action key. If the form has no private field, leave the
> key out and say "available on request".

Open https://consentinel-web-255860737849.us-central1.run.app/try — no login, nothing to install.
That page hands you the sample files. Reading is open; the four steps below each spend a Gemini
call, so add `?k=<ACTION KEY>` to the URL.

1. **Registry → Add permission slip** → `contract.pdf`. Six fields, each with its source sentence.
   Then try `contract-expired.pdf` (lands as expired) and `contract-withholding.pdf` (records voice,
   refuses likeness).
2. **Enforcement Sweep → Run sweep now.** Gemini writes phrases in five languages, Parallel
   searches live. About a minute the first time.
3. **Any finding → Open the case file.** The breached clause, the page snapshot, a draft notice —
   and no send button.
4. **Clearance Check** → `voice-clip.wav` as *voice* for `US` clears; the same file for `BR` is
   blocked. `face-still.jpg` as *face* is blocked.

Also: sweep `/demo/listing-injected`, a page that instructs our agent to call the listing
authorised. The verdict does not move.

Runtime evidence: `evidence/published/` (Parallel calls with `search_id`s) and
`infra/agent_engine/smoke_output.json` (each Agent Engine runtime answering a live call).

---

## Do not say (each of these is false today)

- "We sweep audio, video and images" — discovery is **text only**. Clearance is multimodal; say that.
- "The app calls Agent Engine" / "the whole pipeline runs on Agent Engine" — the runtimes are
  deployed and answer calls, but the app runs the same agents in-process on Cloud Run.
- "We detect deepfakes" or "we verify speakers" — we prove *coverage*, not AI, and Gemini cannot
  prove a voiceprint.
- "Cloud Vision" — it is in `requirements.txt` but no code calls it.
- "Sends takedown notices" — it drafts them.

## Where each claim is backed

| Claim | Evidence |
|---|---|
| Parallel called at runtime via official SDK | `consentinel/tools/parallel_search.py`, `evidence/published/*-calls.log` |
| Four Agent Engine runtimes | `infra/agent_engine/deployed.json`, `smoke_output.json` |
| Five languages / markets | `consentinel/agents/query_planner.py` (en-US, pt-BR, es-MX, ja-JP, hi-IN) |
| Code decides verdicts, no model | `consentinel/agents/reconciler.py` |
| Verbatim-quote rule | extraction validators, `tests/` |
| Injection page doesn't move the verdict | `/demo/listing-injected`, `evalsets/adversarial_injection` |
| Real third parties withheld | `consentinel/sweep.py`, `tests/test_sweep.py` |
| No send path | `Dossier.sendable` is `False` |
| 645 tests | `python -m pytest tests/ -q` on `main`, 27 Oct |
