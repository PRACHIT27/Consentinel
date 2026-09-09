"""Extra demo rows, so the screens show a registry rather than a single grant.

One performer with one grant demonstrates the mechanism and demonstrates
nothing about the judgement. The interesting cases are the ones where two rows
look identical until you read the dates or the territory list:

* a grant that has **expired** — indistinguishable from a live one at a glance,
  and every use relying on it is now unauthorised;
* a grant **expiring in weeks** — the renewal nobody is watching;
* a performer with **no grant on file at all**;
* a **worldwide** grant, so territory is not always the thing that bites;
* an asset from a vendor who **declared nothing**, which stays unchecked rather
  than being assumed fine.

Everything here is invented, and every finding address ends in `.invalid`, a
suffix reserved by RFC 2606 that can never resolve to a real website.

    python -m tools.extra_seed              # add them
    python -m tools.extra_seed --remove     # take them out again

Kept out of `fixtures/seed.json` on purpose: that file is the frozen contract's
worked example and several tests read it. This is demo dressing, and it should
be removable in one command.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from consentinel.store.base import (  # noqa: E402
    Asset,
    ClearanceState,
    Consent,
    DiscoveredVia,
    Finding,
    FindingStatus,
    Modality,
    Performer,
    PermittedUse,
    Verdict,
)

NOW = datetime.now(timezone.utc)


def performers() -> list[Performer]:
    return [
        Performer(id="perf_theo_marchand", name="Théo Marchand",
                  aliases=["T. Marchand"]),
        Performer(id="perf_ines_cabral", name="Inês Cabral", aliases=["I. Cabral"]),
    ]


def consents() -> list[Consent]:
    return [
        # Expires in six weeks. Reads as "active" to anyone not checking dates.
        Consent(
            id="cons_meridian_theo", performer_id="perf_theo_marchand",
            licensee="Meridian Streaming",
            permitted_uses=[PermittedUse.VOICE_SYNTH, PermittedUse.FACE_REPLACE],
            territories=["FR", "BE"],
            valid_from=NOW - timedelta(days=500), valid_to=NOW + timedelta(days=42),
            compensation_trigger="per-episode fee on any synthetic line",
            clause_citations=[{
                "quote": "Licensee may generate a synthetic reproduction of the "
                         "Artist's voice and visual likeness for the Series, in "
                         "France and Belgium.",
                "page": 3}],
        ),
        # Already lapsed. The point of the row.
        Consent(
            id="cons_nordholm_theo", performer_id="perf_theo_marchand",
            licensee="Nordholm Media",
            permitted_uses=[PermittedUse.ARCHIVAL_REUSE],
            territories=["WORLDWIDE"],
            valid_from=NOW - timedelta(days=900), valid_to=NOW - timedelta(days=75),
            compensation_trigger="one-off buyout",
            clause_citations=[{
                "quote": "The Producer may reuse existing recorded performances "
                         "of the Artist for the duration of this Agreement.",
                "page": 2}],
        ),
        # Worldwide and live, so territory is not the only thing that ever bites.
        Consent(
            id="cons_cascade_ines", performer_id="perf_ines_cabral",
            licensee="Cascade Films",
            permitted_uses=[PermittedUse.VOICE_SYNTH],
            territories=["WORLDWIDE"],
            valid_from=NOW - timedelta(days=120), valid_to=NOW + timedelta(days=600),
            compensation_trigger="per-title fee plus residual",
            clause_citations=[{
                "quote": "Producer may synthesise the Artist's voice for dubbing "
                         "in any territory in which the Picture is distributed.",
                "page": 5}],
        ),
    ]


def findings() -> list[Finding]:
    return [
        Finding(
            id="find_0101", performer_id="perf_theo_marchand",
            url="https://example-dubforge.invalid/packs/theo-marchand-fr",
            url_hash="find_0101",
            discovered_via=DiscoveredVia.TEXT, discovered_locale="fr-FR",
            target_territories=["FR"], modality=Modality.VOICE, is_commercial=True,
            evidence_quote="Pack de voix IA Théo Marchand — 29 €, usage commercial illimité.",
            confidence=0.88, verdict=Verdict.UNAUTHORIZED,
            reasoning="unauthorised: Meridian Streaming holds voice synth and face "
                      "replace for BE, FR, but this seller is not the licensee",
            status=FindingStatus.NEW,
        ),
        Finding(
            id="find_0102", performer_id="perf_ines_cabral",
            url="https://example-dublab.invalid/casos/ines-cabral-dublagem",
            url_hash="find_0102",
            discovered_via=DiscoveredVia.TEXT, discovered_locale="pt-BR",
            target_territories=["BR"], modality=Modality.VOICE, is_commercial=True,
            evidence_quote="Dublagem sintética licenciada por Cascade Films com a voz de Inês Cabral.",
            confidence=0.81, verdict=Verdict.AUTHORIZED,
            matched_consent_id="cons_cascade_ines",
            reasoning="authorised: Cascade Films holds voice synth for WORLDWIDE, "
                      "valid to " + (NOW + timedelta(days=600)).date().isoformat(),
            status=FindingStatus.REVIEWED,
        ),
        Finding(
            id="find_0103", performer_id="perf_theo_marchand",
            url="https://example-archive.invalid/collections/marchand-retrospective",
            url_hash="find_0103",
            discovered_via=DiscoveredVia.TEXT, discovered_locale="fr-FR",
            target_territories=[], modality=None, is_commercial=False,
            confidence=0.35, verdict=Verdict.AMBIGUOUS,
            reasoning="the page does not claim anything was synthesised, so no "
                      "verdict about consent can be reached from it",
            status=FindingStatus.NEW,
        ),
        # Refused rather than read. `blocked_unsafe` is not a verdict about the
        # use; it records that we declined to open the address.
        Finding(
            id="find_0104", performer_id="perf_ines_cabral",
            url="https://example-warez.invalid/dl/ines-voicepack",
            url_hash="find_0104",
            discovered_via=DiscoveredVia.TEXT, discovered_locale="pt-BR",
            target_territories=[], verdict=Verdict.AMBIGUOUS,
            reasoning="we did not open this page: the address is on a known-dangerous list",
            status=FindingStatus.BLOCKED_UNSAFE,
        ),
    ]


def assets() -> list[Asset]:
    production = os.environ.get("CONSENTINEL_DEMO_PRODUCTION", "prod_halcyon_nightfall")
    return [
        Asset(id="asset_0714", production_id=production,
              filename="NF_1301_DUB_fr_v02.wav", shot_code="NF_1301_DUB",
              vendor="Meridian Post", invoice_ref="MP-8841",
              performer_id="perf_theo_marchand", synthetic="yes",
              detected_modality=Modality.VOICE,
              clearance_state=ClearanceState.CLEARED,
              matched_consent_id="cons_meridian_theo",
              reasoning="Meridian Streaming holds voice synth for BE, FR — and this "
                        "grant expires in six weeks, so the next delivery may not clear",
              created_at=NOW - timedelta(days=2)),
        Asset(id="asset_0715", production_id=production,
              filename="NF_1288_crowd_v01.exr", shot_code="NF_1288_crowd",
              vendor="Unnamed vendor", performer_id="perf_ines_cabral",
              synthetic="unknown", clearance_state=ClearanceState.UNVERIFIED,
              reasoning="nobody declared how this was made and no grant has been "
                        "matched to it, so it stays unchecked rather than assumed fine",
              created_at=NOW - timedelta(days=1)),
        Asset(id="asset_0716", production_id=production,
              filename="NF_1402_archive_v03.mov", shot_code="NF_1402_archive",
              vendor="Nordholm Media", invoice_ref="NM-2210",
              performer_id="perf_theo_marchand", synthetic="yes",
              detected_modality=Modality.PERFORMANCE,
              clearance_state=ClearanceState.BLOCKED,
              reasoning="Nordholm Media's grant expired 75 days ago, and archival "
                        "reuse never covered synthesising a new performance anyway",
              created_at=NOW - timedelta(hours=6)),
    ]


IDS = {
    "performers": [p.id for p in performers()],
    "consents": [c.id for c in consents()],
    "findings": [f.url_hash for f in findings()],
    "assets": [a.id for a in assets()],
}


def add(store) -> int:
    n = 0
    for performer in performers():
        store.upsert_performer(performer); n += 1
    for consent in consents():
        store.upsert_consent(consent); n += 1
    for finding in findings():
        store.upsert_finding(finding); n += 1
    for asset in assets():
        store.upsert_asset(asset); n += 1
    return n


def remove(project: str, prefix: str = "") -> int:
    """Delete by document id.

    Findings key on `url_hash`, not on `id` — a lesson from the seed loader,
    where deleting by `id` silently removed nothing and the reset looked like it
    had worked.
    """
    from google.cloud import firestore

    db = firestore.Client(project=project)
    n = 0
    for collection, ids in IDS.items():
        for doc_id in ids:
            db.collection(f"{prefix}{collection}").document(doc_id).delete()
            n += 1
    return n


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.extra_seed",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--prefix", default=os.environ.get("CONSENTINEL_PREFIX", ""))
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("set GOOGLE_CLOUD_PROJECT or pass --project", file=sys.stderr)
        return 2

    from consentinel.store.firestore_store import FirestoreStore

    if args.remove:
        print(f"removed {remove(project, args.prefix)} documents")
        return 0

    store = FirestoreStore(project=project, prefix=args.prefix)
    print(f"added {add(store)} demo records to {project}")
    print("two more performers, three grants (one expired, one expiring), "
          "four findings, three assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
