# Notion import

Two CSVs that import straight into Notion as databases. Notion turns each into a table with the
first column as the title property.

**Target page:**
https://app.notion.com/p/3d4c336f6ea080269f6be3522dc991e9?v=3d4c336f6ea0809c8289000c51426a40

## How to import

1. Open the page
2. `/` → **Import** (or drag the CSV onto the page)
3. Choose **CSV**, pick the file
4. Notion creates an inline database

Do `tasks.csv` first, then `user-stories.csv` — the stories reference task IDs.

## After importing — fix the property types

Notion imports everything as text. Change these so the boards work:

**tasks.csv**

| Column | Change to | Values |
|---|---|---|
| Task | Title | *(automatic)* |
| Epic | Select | Foundation, Discovery, Triage & Verdict, Enforcement Output, Clearance, Web App, Consent Ingestion, Deploy & Ops, Demo & Submission, PM |
| Owner | Person | Prachit, Vedant, Swara |
| Status | Status | todo, doing, blocked, done |
| Priority | Select | P0, P1, P2 |
| Estimate | Number | hours |
| Depends On | Relation → this database | — |
| Story | Relation → user stories DB | *(set up after importing stories)* |

**user-stories.csv**

| Column | Change to |
|---|---|
| Story | Title |
| Persona | Select |
| Priority | Select |
| Implemented By | Relation → tasks database |

Relations can't come through a CSV — set `Depends On`, `Story` and `Implemented By` by hand after
both imports. The IDs are already in the cells, so it's copy-and-click, not re-typing.

## Views worth creating

- **Board by Status** — the daily driver
- **Board by Owner** — who's overloaded
- **Table filtered to P0** — what actually has to ship
- **Table filtered to Blocked** — check this first each morning

## Keep them in sync

`TASKS.md` in the repo root is **canonical**. Claude Code sessions read files and cannot see Notion,
so if the two disagree, the repo wins.

Swara's ticket **T-48** is the sync: twice daily, copy status changes from Notion into the Status
section of `CLAUDE.md`. Without that, two Claude sessions will rebuild work that is already done.

Regenerating these CSVs after `TASKS.md` changes is fine — re-import into a fresh database rather
than trying to merge.

## Note on T-47

Don't build the polished architecture diagrams yet. `ARCHITECTURE.md` is not frozen — **T-50** is
the freeze, and it is gated on **T-07**. If Parallel's Extract API returns full page content it
replaces `fetch_page`, which redraws four of the seven diagrams. Import them now as a working draft;
polish after the v1.0.0 bump in `VERSION.md`.
