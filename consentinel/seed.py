"""Load `fixtures/seed.json` into the registry.

    python -m consentinel.seed                  # upsert the fixture
    python -m consentinel.seed --reset          # remove it first, then upsert
    python -m consentinel.seed --prefix test_   # against a throwaway namespace
    python -m consentinel.seed --dry-run        # parse and report, write nothing

Idempotent by construction: every record carries its own id and the store
upserts on it, so running twice leaves the same documents.

Note on `--reset`: the original work unit said "drop and recreate the schema",
which was written when the store was SQLite. Firestore has no schema to drop,
and `FirestoreStore.purge()` deliberately refuses to run without a collection
prefix — an unprefixed purge would wipe the real registry and audit trail. So
`--reset` deletes exactly the documents this fixture defines, by id, and
nothing else. A clean re-seed without a foot-gun.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from consentinel.store.base import Asset, Consent, Finding, Performer
from consentinel.store.codec import from_doc
from consentinel.store.firestore_store import (
    ASSETS,
    CONSENTS,
    FINDINGS,
    PERFORMERS,
    FirestoreStore,
    doc_id_for,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "seed.json"

# (fixture key, record type, store method, collection name)
SECTIONS: tuple[tuple[str, type, str, str], ...] = (
    ("performers", Performer, "upsert_performer", PERFORMERS),
    ("consents", Consent, "upsert_consent", CONSENTS),
    ("findings", Finding, "upsert_finding", FINDINGS),
    ("assets", Asset, "upsert_asset", ASSETS),
)


def load_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def parse(data: dict[str, Any]) -> dict[str, list[Any]]:
    """Turn fixture JSON into records.

    `from_doc` does the work: it coerces enum strings and ISO timestamps from
    the field annotations, ignores keys it does not recognise (the fixture's
    `_comment`), and falls back to defaults for keys it does not find. That is
    the same path Firestore reads take, so the fixture cannot drift from the
    store's own decoding.
    """
    out: dict[str, list[Any]] = {}
    for key, cls, _method, _col in SECTIONS:
        out[key] = [from_doc(cls, row) for row in data.get(key, [])]
    return out


def reset(store: FirestoreStore, records: dict[str, list[Any]]) -> int:
    """Delete only the documents this fixture defines.

    Addresses each document via `doc_id_for`, not `record.id` — a finding lives
    under its `url_hash`, so deleting by `id` silently misses every one of them.
    """
    removed = 0
    for key, _cls, _method, col in SECTIONS:
        for rec in records[key]:
            store._col(col).document(doc_id_for(rec)).delete()
            removed += 1
    return removed


def seed(store: FirestoreStore, records: dict[str, list[Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, _cls, method, _col in SECTIONS:
        upsert = getattr(store, method)
        for rec in records[key]:
            upsert(rec)
        counts[key] = len(records[key])
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="consentinel.seed", description=__doc__.splitlines()[0])
    ap.add_argument("--reset", action="store_true", help="delete the fixture's documents first")
    ap.add_argument("--prefix", default="", help="collection prefix (use for throwaway namespaces)")
    ap.add_argument("--project", default=None, help="GCP project; defaults to GOOGLE_CLOUD_PROJECT")
    ap.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    args = ap.parse_args(argv)

    records = parse(load_fixture(args.fixture))
    total = sum(len(v) for v in records.values())

    if args.dry_run:
        for key, rows in records.items():
            print(f"  {key:<12} {len(rows)}")
        print(f"parsed {total} records from {args.fixture.name}; wrote nothing")
        return 0

    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("set GOOGLE_CLOUD_PROJECT or pass --project", file=sys.stderr)
        return 2

    store = FirestoreStore(project=project, prefix=args.prefix)
    where = f"{project}" + (f" (prefix {args.prefix!r})" if args.prefix else "")

    if args.reset:
        print(f"removed {reset(store, records)} documents from {where}")

    counts = seed(store, records)
    for key, n in counts.items():
        print(f"  {key:<12} {n}")
    print(f"seeded {sum(counts.values())} records into {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
