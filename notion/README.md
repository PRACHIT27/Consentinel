# Notion boards

Two databases, related to each other.

| File | Becomes | Rows |
|---|---|---|
| `stories.csv` | **Stories** — outcomes, written for humans | 61 across 12 epics |
| `tasks.csv` | **Tasks** — the work units engineers actually pick up | 38, WU-00 to WU-37 |

A story says *what should be true when we're done*. A task says *what someone builds this
afternoon*. Several stories usually share one task, which is why they are related rather than
duplicated.

**Target page:**
https://app.notion.com/p/3d4c336f6ea080269f6be3522dc991e9?v=3d4c336f6ea0809c8289000c51426a40

---

## 1. Import

On the page: `/` → **Import** → **CSV**. Do **`tasks.csv` first**, then `stories.csv` — the stories
reference work-unit IDs, so having tasks in place makes the relation easy to wire.

## 2. Make each one a Kanban board

**View → Board → Group by: Status**

A Notion database *is* a Kanban board; table and board are two views of the same data. Nothing to
rebuild.

## 3. Fix the property types

CSV imports everything as plain text. On **both** databases:

| Column | Change to | Values |
|---|---|---|
| **Status** | Status | **Ready · In Progress · Done** |
| **Assignee** | Person | Prachit · Vedant · Swara |
| **Epic** | Select | numbered so they sort correctly |
| **Priority** | Select | P0 · P1 · P2 |
| **Estimate** | Number | hours |

Then on **Tasks**: `Depends On` → Relation to Tasks itself, `Stories` → Relation to Stories.
On **Stories**: `Tasks` → Relation to Tasks.

Relations can't come through a CSV, but the IDs are already in the cells — so it's copy-and-click,
not retyping.

## 4. Card display

Show `Assignee`, `Priority` and `Epic` on the card face. Keep `Description`, `Acceptance` and
`Prompt` hidden — they are what you read when you open the card.

## 5. Views worth having

- **Tasks — Board by Status** — the daily driver
- **Tasks — Board by Assignee** — who is overloaded
- **Tasks — Table filtered to Blocked** — check this every morning
- **Stories — Board by Epic** — progress by area, for the writeup

---

## Shape of the work

| | Stories | Tasks |
|---|---|---|
| Rows | 61 | 38 |
| Estimated | 87h | 85.5h |
| Prachit | 24 | 13 |
| Vedant | 33 | 23 |
| Swara | 4 | 2 |
| P0 / P1 / P2 | 38 / 21 / 2 | 24 / 11 / 2 |

Swara's counts look light because hers are the largest single items — fixtures, video, writeup — and
PM overhead is not ticketed.

**WU-00, the agent harness, blocks every other task.** It is the only thing that should be in
progress at the very start.

## The `Prompt` column

Each task carries `BUILD_PROMPTS.md WU-xx`. That is the paste-ready prompt for a Claude Code
session: which docs to read, which file to write, the rules that bite on that piece, and its
acceptance condition.

**The prompt text is deliberately NOT copied into Notion.** Claude sessions read the repo and cannot
see Notion, so duplicating it would guarantee the two drift apart. Notion tells you *what*; the repo
tells Claude *how*.

## Keeping it in sync

The repo is **canonical**. If Notion and `BUILD_PROMPTS.md` disagree, the repo wins.

Swara's job is the sync in the other direction: twice daily, copy status changes from the boards
into the **Status** section of `CLAUDE.md`. Without that, two Claude sessions will happily rebuild
work that is already finished.

If either CSV is regenerated, re-import into a **fresh** database rather than trying to merge.
