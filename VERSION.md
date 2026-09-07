# Version log

Change record for the **governed files** — the docs and the frozen contract. Every change to one of
them gets an entry here, in the same commit. A `pre-commit` hook enforces it (see
[Enforcement](#enforcement)).

Why: three people are working with separate Claude Code sessions that cannot see each other. This
file is how a session finds out that the contract moved since it last looked.

**Doc set version: `v0.1.0`**
**Architecture status: 🔓 NOT FROZEN** — see [Architecture freeze](#architecture-freeze)

---

## Governed files

Changes to any of these require a VERSION.md entry:

| File | Why it's governed |
|---|---|
| `schema.sql` | Frozen contract — both halves of the build code against it |
| `consentinel/store/base.py` | Frozen contract — interfaces and records |
| `consentinel/tools/contracts.py` | Frozen contract — tool signatures |
| `PRD.md` | Requirements; changes invalidate acceptance criteria |
| `TECHNICAL_DESIGN.md` | Design decisions others are implementing against |
| `ARCHITECTURE.md` | Swara designs Notion diagrams from this |
| `COMPETITION.md` | Rules and submission requirements |
| `RESOURCE_MAP.md` | Which services we use and where |
| `TASKS.md` | Backlog; Swara mirrors into Notion |
| `CLAUDE.md` | The hub every session loads |
| `BUILD_SPEC.md`, `TEAM_BRIEF.md`, `README.md` | Shared understanding |

Ordinary implementation files are **not** governed — commit them freely without touching this file.

## Versioning scheme

`MAJOR.MINOR.PATCH` for the document set as a whole.

- **MAJOR** — a frozen-contract change (schema, interfaces, tool signatures), or an architecture
  change after freeze. Requires all three of us to agree
- **MINOR** — a new document, a new requirement, a new epic, a scope change
- **PATCH** — clarifications, typos, added detail that changes nothing anyone is building against

## Entry format

```
## vX.Y.Z — YYYY-MM-DD — Author
**Files:** the governed files touched
**Type:** MAJOR | MINOR | PATCH
- what changed, and why it matters to someone else
- **Action required:** anything a teammate must do because of this (omit if none)
```

Write the entry for the *reader*, not the record. "Renamed a field" is useless; "renamed
`consents.territory` → `territories` (now a list) — update any query you wrote against it" is what
someone needs.

---

## Architecture freeze

Swara cannot design Notion diagrams against a moving target, so `ARCHITECTURE.md` gets frozen once
the open questions that could change it are answered.

**Blocking:** ticket **T-07** — Parallel's API reference.

- **OQ-5** — if the Extract API returns full page content, it *replaces* our own `fetch_page`.
  That changes diagrams 1, 2, 3 and 5
- **OQ-2** — if a second partner product is permitted and we adopt ClickHouse, that adds a
  component to diagrams 1 and 2

**OQ-1** (locale parameters) affects the query planner's behaviour but not the structure, so it does
not block the freeze.

**Freeze procedure**
1. Vedant answers T-07 and records the answers in `CLAUDE.md`
2. Apply any resulting changes to `ARCHITECTURE.md`
3. All three confirm — in the Notion page or the group chat
4. Bump to **v1.0.0** here, set status to 🔒 FROZEN, and log the sign-off
5. Swara designs from that version. After freeze, a structural change is a MAJOR bump and needs
   all three to agree

Until step 4, treat the diagrams as a working draft. Don't spend hours making them pretty.

---

## Enforcement

A `pre-commit` hook blocks any commit that touches a governed file without also staging
`VERSION.md`.

**Every teammate runs this once after cloning:**

```bash
git config core.hooksPath .githooks
```

That's in the setup steps in `README.md`. Without it the hook is inert — it is a convention with a
safety net, not a wall.

To bypass in a genuine emergency: `git commit --no-verify`. If you use it, add the entry in the next
commit.

---

# Log

Newest first.

## v0.1.0 — 2026-09-07 — Prachit (with Claude)
**Files:** all governed files — initial creation
**Type:** MINOR

- **Frozen contract established:** `schema.sql` (7 tables), `store/base.py` (records, enums,
  `Store`/`Cache`/`EvidenceStore` interfaces), `tools/contracts.py` (`parallel_search`,
  `fetch_page`, `vision_web_detection` signatures, `TriageExtraction` schema)
- **`PRD.md`** — FR-1…FR-8 and TS-1…TS-6 with acceptance criteria, personas, non-goals, milestones,
  open questions
- **`TECHNICAL_DESIGN.md`** — fail-safe principle, scheduler, retry classification and circuit
  breaker, six guardrail layers, prompt-injection defence, storage zones, two cache regimes
- **`COMPETITION.md`** — verbatim rules, accepted SDKs, the Parallel runtime requirement, submission
  checklist, judging criteria, disqualification risks
- **`RESOURCE_MAP.md`** — every hackathon resource mapped to a component. Two decisions recorded:
  deploy agents to Agent Engine; generate demo fixtures with Imagen 3 and TTS so no real person's
  likeness appears in the submission
- **`ARCHITECTURE.md`** — 7 Mermaid diagrams
- **`TASKS.md`** — 49 tickets across 10 epics, critical path, day plan, cut order
- **`BUILD_SPEC.md`**, **`TEAM_BRIEF.md`**, **`README.md`**, **`CLAUDE.md`** hub
- `requirements.txt` corrected to the published ADK install line and the official `parallel-web` SDK

**Action required:**
- Everyone: run `git config core.hooksPath .githooks` after cloning
- Vedant: **T-07** first — it blocks the agent side *and* the architecture freeze
- Swara: import `TASKS.md` and `ARCHITECTURE.md` into the Notion page; hold off on polished design
  until v1.0.0
