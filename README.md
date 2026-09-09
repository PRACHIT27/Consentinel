# Consentinel

**A permission-slip system for AI copies of performers.** Consentinel finds people using an actor's
face or voice without consent, and stops a studio from accidentally shipping something it never had
the rights to.

Built for the Agentic Cinema hackathon — Gemini and Google Cloud Agent Builder, on the **Parallel**
track.

---

## The problem, in plain words

AI can now copy an actor's face and voice. So contracts spell out exactly what may be done with that
copy — for example: *"you may use my voice for this film, in the US and Canada, for three years; you
may not generate a synthetic version of my face."*

Think of that as a **permission slip**. Two things go wrong today.

**Outward — strangers use it.** Websites openly sell "AI voice of [actor]". The performer never
agreed. Their agency finds out by accident, months later, because nothing is watching.

**Inward — the studio loses track of its own slips.** A film is assembled by dozens of outside
vendors, any of whom may have quietly used a generative tool. Before release, someone has to sign
that the production holds the rights to everything in it. Today they establish that by emailing
vendors and hoping.

Both are the same question: **does a permission slip exist for this use?** Nobody has built the tool
that answers it.

That is not hypothetical. In May 2026 an actor sued a studio alleging her teenage face became the
basis for a billion-dollar character — she says she found out seventeen years later, from a clip
circulating online. When SAG-AFTRA challenged the AI Darth Vader voice in *Fortnite*, the striking
detail was that James Earl Jones *had* signed a grant; the fight was over whether it covered that
use. That is precisely the question this system answers.

## Who uses it

| Direction | User | Their question |
|---|---|---|
| **Enforcement** (outward) | Talent agents, agency counsel, estates | Who is using our client's likeness without a permission slip? |
| **Clearance** (inward) | Studio business affairs, post-production supervisors | Can we prove coverage for everything in this delivery? |

A third moment matters for the pitch: when a distributor acquires a finished film, they demand proof
the AI elements were cleared. Consentinel produces that document — so it becomes something you *have
to* hand over, not merely something useful.

## How it works, end to end

1. **Add a permission slip.** Drag in a contract PDF. Gemini fills in six fields — performer,
   licensee, what's permitted, which territories, and the validity window — and quotes the source
   sentence for each one. You correct anything wrong and save.
2. **Sweep.** Gemini writes search phrases in five languages, and Parallel's Search API runs them.
   Audio and video sweeps pull down sample media, because voice cloning is sold as audio samples and
   synthetic endorsements run as video ads.
3. **Judge.** Two separate steps, and the separation is the point. Gemini *reads* each page and
   answers a fixed set of questions. Then **plain code** — no model — checks those answers against
   the registry: is there a grant, does it cover this use, this territory, this date, this licensee?
4. **Build the case file.** For unauthorised findings: a permanent snapshot of the page, the exact
   breached clause, and a draft takedown notice. **There is no send button.** A person sends it.
5. **Check your own delivery.** Upload a clip from your film and the *same rules* answer: fine to
   ship, blocked, or unverified.

Steps 1–4 catch outsiders. Step 5 catches yourself. One registry, two directions — which is why this
is one product rather than two.

## What we do not claim

Stated plainly, because a clean boundary is worth more than an overclaim a judge can puncture.

- **No deepfake forensics.** We find unauthorised *commercial offerings*, which announce themselves
  in text because they have to sell something.
- **No speaker verification.** Gemini can transcribe and describe audio and video; it cannot prove a
  voiceprint belongs to a person. Media analysis is corroborating evidence that raises confidence,
  never identity proof.
- **No legal advice.** We surface evidence and rule mismatches. Counsel decides.
- **No platform-wide monitoring.** That needs firehose access — the enterprise path, not this build.
- **On our own deliverables we prove coverage, not AI.** Every asset defaults to `unverified` and
  only becomes `cleared` when a grant covers it. Which is exactly why imperfect detection is still
  useful.

## Architecture in one paragraph

A consent registry in Firestore is the spine. Two ADK pipelines read from it in opposite directions
and share one deterministic rule engine. Discovery fans out across text, audio, video and image
sweeps. Everything that reads untrusted content — page triage, media triage — runs in an isolated
Agent Runtime deployment holding **no tools, no secrets and no write access**, and the reconciler
that decides verdicts never receives page text at all. So a page that tries to instruct our agent
cannot reach a decision. Model Armor screens injection and PII on top of that. Evidence snapshots
live in a bucket where no principal in the system holds delete permission.

Diagrams: [ARCHITECTURE.md](ARCHITECTURE.md). Full design: [DESIGN.md](DESIGN.md).

## Documentation

| File | What it is |
|---|---|
| [CLAUDE.md](CLAUDE.md) | The hub. Rules, ownership, status. Loads automatically in Claude Code |
| [PRD.md](PRD.md) | Requirements with acceptance criteria |
| [DESIGN.md](DESIGN.md) | Part I pipeline design, Part II runtime, IAM and operations |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Diagrams |
| [COMPETITION.md](COMPETITION.md) | Hackathon rules, submission checklist, resource mapping |
| [BUILD_PROMPTS.md](BUILD_PROMPTS.md) | What to build, one ready-to-paste prompt per work unit |
| [VERSION.md](VERSION.md) | Change log for the docs and the frozen contract |

## Setup

```bash
git clone https://github.com/PRACHIT27/Consentinel.git
cd Consentinel
git config core.hooksPath .githooks
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

The `core.hooksPath` line is not optional — it enables the hook that keeps [VERSION.md](VERSION.md)
in step with changes to the docs and the frozen contract.

Verify the ADK, Gemini, Cloud Vision and Model Armor API surfaces against current Google docs before
building on them; a few names in `tools/contracts.py` were written from memory.

## Demo data

`fixtures/seed.json` seeds a **fictional** performer and licensee, and the reference images and
cloned-voice sample are generated with Gemini image generation and Gemini TTS. Every synthetic asset in the
submission is one we made, of a person who does not exist — so no real individual's likeness appears
anywhere, and demo safety is satisfied by construction rather than by disclaimer.

## Team

Prachit (foundation and app) · Vedant (agents and tools) · Swara (PM, docs, demo).
Ownership and current status live in [CLAUDE.md](CLAUDE.md).

## License

MIT — see [LICENSE](LICENSE).
