"""Run a real sweep, end to end, and write what it finds to the registry.

Every piece of this existed already and nothing ran them as a chain. This is
the chain:

    QueryPlanner  ->  Parallel Search  ->  web_risk  ->  fetch_page
                  ->  Triage  ->  Reconciler  ->  Firestore

    python -m consentinel.sweep --performer perf_mira_vance
    python -m consentinel.sweep --dry-run          # plan and search, write nothing
    python -m consentinel.sweep --allow-third-party

**Demo safety is the reason for the `--only-hosts` default.** A real sweep finds
real websites, and `CLAUDE.md` says we do not publish authorisation verdicts
naming real third parties — the repo is public and the video is public. So by
default a finding is *recorded* only for a host we control or a reserved
`.invalid` address. Third-party candidates are still searched for, still
counted, and still reported on stdout; they simply do not get written to a
registry that a public page renders.

That is a publication rule, not a shortcut. What runs is real either way:
Parallel is called for each locale, `fetch_page` fetches over the network with
its own guards, Triage reads the page with a real model call and must quote it
verbatim, and the verdict comes from the deterministic reconciler reading the
registry. The planted page at `/demo/listing` is ours and its performer is
invented, which is what makes it safe to show on camera.

Fails toward doubt throughout: a page we cannot read, a model that will not
answer, a quote that is not verbatim — each leaves the finding `ambiguous`,
never `authorized`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlsplit

from consentinel.agents.query_planner import QueryPlanner, deterministic_plan
from consentinel.agents.reconciler import Reconciler
from consentinel.agents.text_sweep import TextSweep
from consentinel.agents.triage import Triage
from consentinel.harness.ports import HarnessDeps
from consentinel.store.base import Consent, Finding, Performer, Store
from consentinel.tools.fetch_page import PageFetcher
from consentinel.tools.web_risk import WebRiskCheck

log = logging.getLogger("consentinel.sweep")

# Hosts whose verdicts we are willing to publish. Ours, and the reserved suffix
# that can never resolve to a real website.
SAFE_SUFFIXES = (".run.app", ".invalid", "localhost", "127.0.0.1")


@dataclass
class SweepSummary:
    """What the run did, in the terms a person watching it needs."""

    performer_id: str
    locales: int = 0
    batches: int = 0
    searched: int = 0            # raw results Parallel returned
    candidates: int = 0          # after dedupe
    recorded: int = 0            # findings written
    withheld: int = 0            # third-party candidates, not written
    read: int = 0                # pages Triage read
    refused: int = 0             # pages we would not or could not open
    searches: int = 0            # search calls made
    searches_cached: int = 0     # ...of which were served from the cache
    verdicts: dict[str, int] = field(default_factory=dict)
    degraded: bool = False
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        counts = ", ".join(f"{n} {v}" for v, n in sorted(self.verdicts.items())) or "none"
        live = self.searches - self.searches_cached
        return (f"{self.batches} batches over {self.locales} locales · "
                f"{self.searched} results ({live} live, {self.searches_cached} cached) · "
                f"{self.candidates} candidates · "
                f"{self.read} read, {self.refused} refused · "
                f"{self.recorded} recorded, {self.withheld} withheld · {counts}")


def publishable(url: str) -> bool:
    """May we write a verdict about this address to the public registry?"""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == s or host.endswith(s) for s in SAFE_SUFFIXES)


def run(
    store: Store,
    performer: Performer,
    consents: Sequence[Consent],
    *,
    deps: Optional[HarnessDeps] = None,
    extra_urls: Sequence[str] = (),
    allow_third_party: bool = False,
    dry_run: bool = False,
    use_model_planner: bool = True,
) -> SweepSummary:
    """One sweep. Returns a summary; raises nothing a caller has to catch."""
    deps = deps or HarnessDeps()
    summary = SweepSummary(performer_id=performer.id)

    # 1 — the plan. Gemini writes the phrases; a deterministic fallback covers
    # a model failure, so a sweep is never blocked on one.
    #
    # `use_model_planner=False` is not a lesser mode, it is the one that hits
    # the warm cache. Cache keys include the query text, so a model-written plan
    # asks Parallel something slightly different every time and misses every
    # cached call — which is fine with a Parallel key and useless without one.
    # The deterministic plan is stable, so it replays the recorded calls.
    plan = (QueryPlanner(deps=deps).plan(performer, consents) if use_model_planner
            else deterministic_plan(performer, consents))
    summary.locales = len(plan.locales)
    summary.batches = len(plan.batches)
    if plan.degraded:
        summary.degraded = True
        summary.notes.append("the plan came back degraded; searching anyway")

    # 2 — discovery. Real Parallel calls, one per batch.
    #
    # `NullStore` here on purpose: TextSweep writes a bare finding per
    # candidate, and we would rather write once, after the verdict, than write a
    # verdict-less row now and update it later. A half-written finding on a
    # public screen reads as "checked and found nothing wrong".
    from consentinel.agents.text_sweep import FindingStore

    class _Collect(FindingStore):                      # type: ignore[misc]
        def upsert_finding(self, finding: Finding) -> Finding:
            return finding

    report = TextSweep(store=_Collect(), deps=deps).run(performer, plan)
    summary.searched = report.raw_results
    # Say which searches were live and which were replayed. A sweep that
    # finishes in two seconds looks either impressive or fake, and the honest
    # answer is neither: the cache is shared through Firestore, so a query
    # someone already ran comes back instantly. Verdicts are never cached
    # (hard rule 7), so the answer is recomputed either way.
    summary.searches = report.batches_run
    summary.searches_cached = report.from_cache
    if report.degraded:
        summary.degraded = True
        summary.notes.append(f"discovery degraded: {report.abort_reason or 'some batches failed'}")

    candidates = list(report.candidates)
    for url in extra_urls:
        candidates.append(_manual_candidate(url, plan))
    summary.candidates = len(candidates)

    if dry_run:
        summary.notes.append("dry run: nothing was read and nothing was written")
        return summary

    # 3 — read each candidate, then judge it.
    fetcher = PageFetcher(deps=deps, risk=WebRiskCheck(deps=deps))
    triage = Triage(deps=deps)
    reconciler = Reconciler(deps=deps)

    for candidate in candidates:
        writable = allow_third_party or publishable(candidate.url)
        if not writable:
            # Counted and named in the log, not written. See the module note.
            summary.withheld += 1
            log.info("withheld (third party): %s", candidate.url)
            continue

        outcome = fetcher.fetch_detailed(candidate.url, candidate.locale,
                                         subject_id=performer.id)
        if not outcome.ok or outcome.snapshot is None:
            # Recorded, not dropped. "We could not look" has to be
            # distinguishable from "we looked and found nothing" (hard rule 10),
            # and a candidate that silently disappears is indistinguishable from
            # one that was never found. `FetchOutcome.status` already says which
            # refusal it was — a known-dangerous address comes back as
            # `blocked_unsafe`, which the findings screen badges "not opened".
            summary.refused += 1
            log.info("refused %s: %s", candidate.url, outcome.reason)
            _record(store, candidate, performer, verdict=None,
                    reasoning=f"we did not open this page: {outcome.reason}",
                    status=outcome.status)
            summary.recorded += 1
            summary.verdicts["unclear"] = summary.verdicts.get("unclear", 0) + 1
            continue

        reading = triage.read(outcome.snapshot, performer)
        if not (reading.ok and reading.extraction):
            summary.refused += 1
            log.info("unreadable %s: %s", candidate.url, reading.reason)
            _record(store, candidate, performer, verdict=None,
                    reasoning=f"the page could not be read: {reading.reason}")
            summary.recorded += 1
            summary.verdicts["unclear"] = summary.verdicts.get("unclear", 0) + 1
            continue

        summary.read += 1
        result = reconciler.for_extraction(reading.extraction, performer.id,
                                           consents, actor=None)
        _record(store, candidate, performer, verdict=result,
                extraction=reading.extraction)
        summary.recorded += 1
        key = getattr(result.verdict, "value", str(result.verdict))
        summary.verdicts[key] = summary.verdicts.get(key, 0) + 1

    return summary


def _manual_candidate(url: str, plan) -> object:
    """A candidate we supplied rather than discovered.

    Used for the planted page. It goes through exactly the same fetch, the same
    triage and the same rules as anything the search found — the only
    difference is how its address arrived.
    """
    from consentinel.agents.text_sweep import Candidate, url_hash

    locale = plan.locales[0] if plan.locales else None
    return Candidate(url=url, url_hash=url_hash(url), locale=locale,
                     modality="voice", batch_index=-1,
                     title="supplied on the command line", excerpts=())


def _record(store: Store, candidate, performer: Performer, *, verdict,
            extraction=None, reasoning: Optional[str] = None,
            status=None) -> None:
    """Write the finding, verdict included, in one go."""
    from consentinel.store.base import DiscoveredVia, FindingStatus, Modality

    fields = {}
    if verdict is not None:
        fields = verdict.to_finding_fields()
    else:
        fields = {"verdict": None, "matched_consent_id": None,
                  "reasoning": reasoning or "not established", "evidence_quote": None}

    modality = None
    territories: list[str] = []
    confidence = None
    commercial = None
    if extraction is not None:
        try:
            modality = Modality(extraction.modality) if extraction.modality else None
        except ValueError:
            modality = None
        territories = list(extraction.target_territories or [])
        confidence = extraction.confidence
        commercial = extraction.is_commercial

    store.upsert_finding(Finding(
        id=candidate.url_hash,
        performer_id=performer.id,
        url=candidate.url,
        url_hash=candidate.url_hash,
        discovered_via=DiscoveredVia.TEXT,
        discovered_locale=str(candidate.locale) if candidate.locale else "",
        target_territories=territories,
        modality=modality,
        is_commercial=commercial,
        confidence=confidence,
        status=status or (FindingStatus.REVIEWED if verdict is not None
                          else FindingStatus.NEW),
        **fields,
    ))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="consentinel.sweep",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--performer", default="perf_mira_vance")
    ap.add_argument("--url", action="append", default=[],
                    help="also read this address (the planted demo page)")
    ap.add_argument("--allow-third-party", action="store_true",
                    help="record verdicts about hosts we do not control. Read "
                         "CLAUDE.md Demo safety before using this on a public "
                         "registry")
    ap.add_argument("--dry-run", action="store_true", help="plan and search only")
    ap.add_argument("--deterministic-plan", action="store_true",
                    help="skip the model planner and use the stable plan. This "
                         "is what replays the recorded Parallel calls from the "
                         "warm cache, so it is the mode to demo on without a "
                         "Parallel key")
    ap.add_argument("--prefix", default=os.environ.get("CONSENTINEL_PREFIX", ""),
                    help="collection prefix, for sweeping into a throwaway namespace")
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Read `.env` before anything else touches the environment.
    #
    # Without this the sweep still *runs* — and that is the trap. The Parallel
    # key is missing so discovery serves from cache, and `GOOGLE_GENAI_USE_VERTEXAI`
    # is missing so every model call asks for a Gemini API key, fails, and the
    # planner falls back to its deterministic plan. The result is a sweep that
    # reports `degraded=True` and looks like a broken pipeline when the only
    # thing wrong is an unread file. Cloud Run sets real environment variables
    # and has no `.env`, so this only ever matters on a laptop.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    args.project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")

    if not args.project:
        print("set GOOGLE_CLOUD_PROJECT or pass --project", file=sys.stderr)
        return 2

    from consentinel.audit import FirestoreAudit
    from consentinel.cache import FirestoreCache
    from consentinel.obs import JsonLogger, Metrics, tracer_for
    from consentinel.store.firestore_store import FirestoreStore

    store = FirestoreStore(project=args.project, prefix=args.prefix)
    performer = next((p for p in store.list_performers() if p.id == args.performer), None)
    if performer is None:
        print(f"no performer {args.performer!r} in the registry; "
              f"run python -m consentinel.seed first", file=sys.stderr)
        return 2
    consents = store.list_consents(performer.id)

    logger = JsonLogger(service="consentinel-sweep")
    deps = HarnessDeps(
        audit=FirestoreAudit(store, subject_type="finding"),
        cache=FirestoreCache(project=args.project, prefix=args.prefix),
        tracer=tracer_for(),
        metrics=Metrics(logger),
    )

    print(f"sweeping for {performer.name} ({len(consents)} grant(s) on file)")
    summary = run(store, performer, consents, deps=deps, extra_urls=args.url,
                  allow_third_party=args.allow_third_party, dry_run=args.dry_run,
                  use_model_planner=not args.deterministic_plan)

    print(summary.line())
    for note in summary.notes:
        print(f"  note: {note}")
    if summary.withheld and not args.allow_third_party:
        print(f"  {summary.withheld} third-party candidate(s) were found and not "
              f"recorded — see CLAUDE.md Demo safety")
    return 0 if not summary.degraded else 1


if __name__ == "__main__":
    raise SystemExit(main())
