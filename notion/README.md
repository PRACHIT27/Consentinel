# Notion board

**`stories.csv`** is the board — 47 stories across 10 epics, each written to be understood without
this conversation. Import it, switch to Board view, done.

`tasks.csv` is the finer-grained engineering breakdown (50 tickets). Optional — use it only if you
want sub-task tracking under the stories. The `Tasks` column on each story already links them.

**Target page:**
https://app.notion.com/p/3d4c336f6ea080269f6be3522dc991e9?v=3d4c336f6ea0809c8289000c51426a40

---

## 1. Import

On the page: `/` → **Import** → **CSV** → `stories.csv`. Notion creates a table.

## 2. Make it a Kanban board

**View → Board → Group by: Status**

That is the whole trick — a Notion database *is* a Kanban board. Table and Board are two views of
the same data, so there is nothing to rebuild. The table you saw before was just the default view.

## 3. Fix the property types

CSV imports everything as plain text. Change these four and the board works properly:

| Column | Change to | Values |
|---|---|---|
| **Status** | Status | **Ready · In Progress · Done** |
| **Assignee** | Person | Prachit · Vedant · Swara |
| **Epic** | Select | the 10 epics, already numbered so they sort correctly |
| **Priority** | Select | P0 · P1 · P2 |
| **Estimate** | Number | hours |

Everything starts in **Ready**. Optionally add a **Blocked** column — a few stories depend on
others, and it is useful to see them parked rather than buried in Ready.

## 4. Card display

On the board view, **Properties** → show `Assignee`, `Priority` and `Epic` on the card face. Keep
`Description` and `Acceptance` hidden — they are long, and they are what you read when you open the
card.

## 5. Views worth having

- **Board by Status** — the daily driver
- **Board by Assignee** — who is overloaded
- **Board by Epic** — progress per area
- **Table filtered to P0** — what actually has to ship

---

## What's in each card

| Field | What it holds |
|---|---|
| Story | Written as an outcome, not a task — "Never create a duplicate finding" |
| Description | Two to four sentences: what it is, and *why it matters*. The reasoning is included on purpose, so nobody has to reconstruct it |
| Acceptance | The one concrete condition that means it is finished |
| Tasks | The `T-xx` ticket IDs in `BUILD_PROMPTS.md`, for anyone wanting implementation detail |
| Epic · Assignee · Priority · Estimate | Board metadata |

## Shape of the work

| | |
|---|---|
| Epics | 10 (3–6 stories each) |
| Stories | 47 |
| P0 / P1 / P2 | 31 / 14 / 2 |
| Estimated | 64 hours total |
| Vedant / Prachit / Swara | 24 / 19 / 4 stories |

64 hours across three people over two days is tight but survivable — **provided the two P2s stay
cut** and nobody gold-plates. Swara's four look light because hers are the largest single items
(fixtures, video, writeup) and because PM overhead is not ticketed.

## Keeping it in sync

`BUILD_PROMPTS.md` and this folder are **canonical**. Claude Code sessions read repository files and cannot
see Notion, so if the two ever disagree, the repo wins.

Swara's job is the sync in the other direction: twice daily, copy status changes from the Notion
board into the **Status** section of `CLAUDE.md`. Without that, two Claude sessions will happily
rebuild work that is already finished.

If `stories.csv` is regenerated later, re-import into a fresh database rather than trying to merge.
