"""Put the database into a known state for recording. Run it between takes.

    python -m tools.demo_reset                  # the state to record against
    python -m tools.demo_reset --with-mira-grant  # if the live upload fails
    python -m tools.demo_reset --show           # count what is there now

**What it leaves you with**

    performers  Mira Vance, Théo Marchand, Inês Cabral
    consents    Théo x2 (one expired, one expiring), Inês x1 worldwide
                — and *nothing* for Mira Vance
    findings    none
    assets      none
    dossiers    none

Mira's grant is deliberately absent. It makes the first thirty seconds of the
demo do real work: the registry visibly has no permission slip for her, you
upload the contract on camera, and every verdict after that rests on the row
you just created rather than on something that was already sitting there. The
other two performers stay so the registry looks like a registry, and because an
expired grant next to a live one is the thing worth pointing at.

Findings and assets start empty so that the sweep and the clearance uploads are
visibly the things that fill them. An empty enforcement screen that fills up on
camera is worth more than a full one that was full before you arrived.

**The safety net.** If the contract upload misbehaves mid-recording, every later
verdict turns into "no grant on file" and the demo falls apart. Rather than
re-shoot from cold, run `--with-mira-grant`: it puts the Halcyon grant back in
five seconds and you carry on from step 2.

Nothing here touches the `live_` namespace, which is where the
`--allow-third-party` sweep writes.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COLLECTIONS = ("performers", "consents", "findings", "assets", "dossiers")

# Wiped every time. These are the screens the demo fills up on camera, plus the
# case files drafted from them.
CLEARED = ("findings", "assets", "dossiers")


def counts(db, prefix: str = "") -> dict[str, int]:
    return {c: len(list(db.collection(f"{prefix}{c}").stream())) for c in COLLECTIONS}


def wipe(db, collection: str, prefix: str = "") -> int:
    n = 0
    for doc in db.collection(f"{prefix}{collection}").stream():
        doc.reference.delete()
        n += 1
    return n


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.demo_reset", description=__doc__.splitlines()[0])
    ap.add_argument("--with-mira-grant", action="store_true",
                    help="also restore the Halcyon grant — the safety net for a "
                         "recording where the live contract upload went wrong")
    ap.add_argument("--show", action="store_true", help="count what is there and stop")
    ap.add_argument("--prefix", default=os.environ.get("CONSENTINEL_PREFIX", ""))
    ap.add_argument("--project", default=None)
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("set GOOGLE_CLOUD_PROJECT or pass --project", file=sys.stderr)
        return 2

    from google.cloud import firestore

    from consentinel.store.firestore_store import FirestoreStore
    from tools import extra_seed

    db = firestore.Client(project=project)

    if args.show:
        for name, n in counts(db, args.prefix).items():
            print(f"  {name:12} {n}")
        return 0

    for collection in CLEARED:
        print(f"  cleared {wipe(db, collection, args.prefix):>3} from {collection}")

    # Rebuild the cast from scratch, so a half-finished take on camera — an
    # orphan performer created by a save that failed later, say — does not
    # survive into the next one.
    for collection in ("performers", "consents"):
        wipe(db, collection, args.prefix)

    store = FirestoreStore(project=project, prefix=args.prefix)
    for performer in extra_seed.performers():
        store.upsert_performer(performer)
    for consent in extra_seed.consents():
        store.upsert_consent(consent)

    from consentinel.seed import load_fixture, parse

    records = parse(load_fixture())
    mira = next(p for p in records["performers"] if p.id == "perf_mira_vance")
    store.upsert_performer(mira)
    print("  restored 3 performers and 3 grants (none for Mira Vance)")

    if args.with_mira_grant:
        for consent in records["consents"]:
            store.upsert_consent(consent)
        print("  safety net: the Halcyon grant is back")

    print()
    for name, n in counts(db, args.prefix).items():
        print(f"  {name:12} {n}")
    print("\nready to record. Registry shows Mira Vance with no grant on file"
          + (" — except the one you just restored." if args.with_mira_grant else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
