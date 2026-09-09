"""WU-07 — TextSweep. Runs a plan, collects candidates, writes findings.

FR-2.3 (execute every query against Parallel at runtime), FR-2.5 (deduplicate
on `url_hash`; re-running a sweep must not create duplicates) and FR-2.6 (log
every call — WU-05 does that per call, this module adds one summary row).

**No model call.** `DESIGN.md` Part II 2.1 lists TextSweep as an `LlmAgent`
holding `parallel_search`. It is deterministic here instead: QueryPlanner
already did the thinking, and what remains is "call these eight batches,
normalise, dedupe, upsert". A model choosing which of its own planned queries
to run would add non-determinism to a demo for no coverage gain, and CLAUDE.md
prefers deterministic composition for exactly that reason. Flagged in the PR
rather than changed silently — say so if you disagree and it becomes an
`LlmAgent` whose tool loop runs the same batches.

Two fields that are easy to confuse, per WU-07:

* `discovered_locale` — where we searched **from**. This module sets it.
* `target_territories` — where the offering is **aimed**. Triage sets it, by
  reading the page. Leaving it empty here is correct, not an omission.

`modality` is likewise left `None`. We searched a modality; we have not
established that the page depicts one. Asserting it from the batch would put an
unverified claim in the registry. The searched modality is in the report, not
on the row.

Excerpts are not persisted at all: they are LLM-selected and truncated, and
evidence is our own immutable snapshot (WU-15). They ride along in the report
for triage to prioritise with.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from consentinel.agents.query_planner import CANDIDATE_BUDGET, SearchBatch, SearchPlan
from consentinel.harness import HarnessDeps
from consentinel.store.base import (
    DiscoveredVia,
    Finding,
    FindingStatus,
    Locale,
    Performer,
)
from consentinel.tools.contracts import SearchResponse
from consentinel.tools.parallel_search import ParallelSearch, SourcePolicy, is_degraded

AGENT_NAME = "TextSweep"

DEFAULT_CONCURRENCY = 4
"""Four batches in flight. Threads, not processes: every batch is a blocked HTTP
call. Higher is not faster once Parallel starts rate-limiting, and a 429 costs
the sweep more than the wait saved."""

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src",
})
"""Per-click noise. Two links to one listing differ only by these, and without
dropping them the same page becomes three findings — the exact failure FR-2.5
names."""

_DEFAULT_PORTS = {"http": "80", "https": "443"}

log = logging.getLogger("consentinel.text_sweep")


class FindingStore(Protocol):
    """The one Store method this module needs.

    A protocol rather than the `Store` ABC so tests can pass a five-line fake,
    and so this file does not care which implementation is behind it.
    """

    def upsert_finding(self, finding: Finding) -> Finding: ...


# --------------------------------------------------------------------------
# URL normalisation — the whole of deduplication rests on this
# --------------------------------------------------------------------------

def normalise_url(url: str) -> str:
    """Canonical form of a URL for identity purposes.

    Lowercase the scheme and host, drop the default port, drop the fragment,
    sort the query parameters and remove tracking ones. Everything else is left
    alone: path case can be significant, and `?page=2` is a different page.

    Not merged: `www.` and the bare host, `http` and `https`. They are usually
    the same page and occasionally are not, and a wrong merge silently hides a
    finding — which is worse than one duplicate row a human can dismiss.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "//" not in raw.split("?", 1)[0][:8]:
        raw = f"https://{raw}"          # bare host: give it a scheme to parse

    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")         # /a/ and /a are one page

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))   # "" drops the fragment


def url_hash(url: str) -> str:
    """`sha256` of the normalised URL. This is a finding's identity (FR-2.5)."""
    return hashlib.sha256(normalise_url(url).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# What a sweep produces
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One deduplicated page, before it becomes a `Finding`.

    Carries the search-time context that does not belong in the registry:
    which batch found it, what we were looking for, and the excerpts.
    """

    url: str
    url_hash: str
    locale: Locale
    modality: str
    batch_index: int
    title: Optional[str] = None
    publish_date: Optional[str] = None
    excerpts: tuple[str, ...] = ()


@dataclass
class SweepReport:
    performer_id: str
    sweep_id: str
    candidates: tuple[Candidate, ...] = ()
    findings: tuple[Finding, ...] = ()
    batches_planned: int = 0
    batches_run: int = 0
    batches_degraded: tuple[str, ...] = ()
    raw_results: int = 0
    duplicates_collapsed: int = 0
    capped: bool = False
    plan_degraded: bool = False
    write_failures: int = 0
    from_cache: int = 0
    duration_s: float = 0.0

    @property
    def degraded(self) -> bool:
        """True if any part of the sweep could not be completed.

        A caller must be able to tell "we swept and found nothing" from "the
        sweep did not finish" (hard rule 10), so this is deliberately broad:
        a degraded plan, one failed batch, or one failed write all count.
        """
        return bool(self.plan_degraded or self.batches_degraded
                    or self.write_failures)

    def as_dict(self) -> dict[str, Any]:
        return {
            "performer_id": self.performer_id,
            "sweep_id": self.sweep_id,
            "findings": len(self.findings),
            "batches_planned": self.batches_planned,
            "batches_run": self.batches_run,
            "batches_degraded": list(self.batches_degraded),
            "raw_results": self.raw_results,
            "duplicates_collapsed": self.duplicates_collapsed,
            "capped": self.capped,
            "plan_degraded": self.plan_degraded,
            "write_failures": self.write_failures,
            "from_cache": self.from_cache,
            "degraded": self.degraded,
            "duration_s": round(self.duration_s, 4),
        }


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

def exclude_domains_from_env() -> tuple[str, ...]:
    """`CONSENTINEL_EXCLUDE_DOMAINS=a.example,b.example`.

    DESIGN Part III calls a host exclusion the cheapest defence of all: a
    known-bad host that never enters the results never has to be risk-checked,
    fetched or triaged. It lives in the environment rather than in code because
    the list is an operating decision made during a sweep, not a constant.

    Empty by default, deliberately. **An exclusion silently hides findings** —
    the one failure mode this product cannot tolerate — so nothing is excluded
    unless a person says so.
    """
    raw = os.environ.get("CONSENTINEL_EXCLUDE_DOMAINS", "")
    return tuple(d.strip().lower() for d in raw.split(",") if d.strip())


@dataclass
class TextSweep:
    store: FindingStore
    search: Optional[ParallelSearch] = None
    deps: HarnessDeps = field(default_factory=HarnessDeps)
    max_candidates: int = CANDIDATE_BUDGET
    concurrency: int = DEFAULT_CONCURRENCY
    exclude_domains: Optional[tuple[str, ...]] = None   # None -> read the env
    after_date: Optional[str] = None                    # YYYY-MM-DD, freshness

    def _searcher(self) -> ParallelSearch:
        if self.search is None:
            self.search = ParallelSearch(deps=self.deps)
        return self.search

    # ------------------------------------------------------------------

    def run(self, performer: Performer, plan: SearchPlan,
            sweep_id: Optional[str] = None) -> SweepReport:
        """Execute every batch, deduplicate, upsert. Never raises."""
        started = time.monotonic()
        sweep_id = sweep_id or f"sweep_{uuid.uuid4().hex[:12]}"
        batches = list(plan.batches)

        responses = self._run_batches(batches, plan, sweep_id, performer)
        candidates, raw, duplicates, capped, degraded_batches, cached = \
            self._collect(batches, responses)
        findings, write_failures = self._persist(performer, candidates)

        report = SweepReport(
            performer_id=performer.id, sweep_id=sweep_id,
            candidates=candidates, findings=findings,
            batches_planned=len(batches),
            batches_run=len(batches) - len(degraded_batches),
            batches_degraded=degraded_batches,
            raw_results=raw, duplicates_collapsed=duplicates, capped=capped,
            plan_degraded=plan.degraded, write_failures=write_failures,
            from_cache=cached, duration_s=time.monotonic() - started,
        )
        self._audit(report)
        self._log(report)
        return report

    # ------------------------------------------------------------------

    def _run_batches(self, batches: Sequence[SearchBatch], plan: SearchPlan,
                     sweep_id: str, performer: Performer) -> list[Any]:
        """One `parallel_search` call per batch, a few in flight at a time.

        Results come back in plan order regardless of which finished first, so
        two identical sweeps produce identically ordered findings — which is
        what makes the cap reproducible instead of a race.
        """
        searcher = self._searcher()
        excluded = (self.exclude_domains if self.exclude_domains is not None
                    else exclude_domains_from_env())
        policy = (SourcePolicy(exclude_domains=excluded, after_date=self.after_date)
                  if (excluded or self.after_date) else None)

        def one(batch: SearchBatch) -> Any:
            return searcher.search_detailed(
                objective=batch.objective,
                search_queries=list(batch.search_queries),
                locale=batch.locale,
                max_results=plan.results_per_batch,
                session_id=sweep_id,          # ties the sweep together
                subject_id=performer.id,
                source_policy=policy,
            )

        if not batches:
            return []
        workers = max(1, min(self.concurrency, len(batches)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, batches))   # map preserves input order

    def _collect(self, batches: Sequence[SearchBatch], responses: Sequence[Any]
                 ) -> tuple[tuple[Candidate, ...], int, int, bool,
                            tuple[str, ...], int]:
        seen: set[str] = set()
        candidates: list[Candidate] = []
        raw = duplicates = cached = 0
        capped = False
        degraded: list[str] = []

        for index, (batch, result) in enumerate(
                zip(batches, responses, strict=True)):
            if getattr(result, "from_cache", False):
                cached += 1
            response: Optional[SearchResponse] = getattr(result, "value", None)
            if not getattr(result, "ok", False) or response is None \
                    or is_degraded(response):
                # One dead batch degrades the sweep; the other seven still count.
                degraded.append(f"{batch.locale}/{batch.modality}")
                continue

            for item in response.results:
                raw += 1
                digest = url_hash(item.url)
                if not digest or not normalise_url(item.url):
                    continue
                if digest in seen:
                    duplicates += 1
                    continue
                if len(candidates) >= self.max_candidates:
                    capped = True
                    break
                seen.add(digest)
                candidates.append(Candidate(
                    url=item.url, url_hash=digest, locale=batch.locale,
                    modality=batch.modality, batch_index=index,
                    title=item.title, publish_date=item.publish_date,
                    excerpts=tuple(item.excerpts),
                ))

        return tuple(candidates), raw, duplicates, capped, tuple(degraded), cached

    def _persist(self, performer: Performer, candidates: Sequence[Candidate]
                 ) -> tuple[tuple[Finding, ...], int]:
        """Upsert each candidate. The store owns `first_seen` / `last_checked`.

        Those two are left `None` on purpose: the store preserves `first_seen`
        across re-sweeps and refreshes `last_checked`, and setting them here
        would overwrite the answer to "when did we first see this".
        """
        findings: list[Finding] = []
        failures = 0
        for c in candidates:
            finding = Finding(
                # id == url_hash: a deterministic id keeps the upsert idempotent
                # whichever field a given Store implementation keys on. A uuid
                # here would create a second row per sweep.
                id=c.url_hash,
                performer_id=performer.id,
                url=c.url,
                url_hash=c.url_hash,
                discovered_via=DiscoveredVia.TEXT,
                discovered_locale=str(c.locale),   # where we searched FROM
                target_territories=[],             # triage fills this in
                modality=None,                     # unverified until triage reads it
                status=FindingStatus.NEW,
            )
            try:
                findings.append(self.store.upsert_finding(finding))
            except Exception as exc:  # noqa: BLE001 - a failed write degrades, never crashes
                failures += 1
                log.warning("%s: could not upsert %s: %s", AGENT_NAME, c.url, exc)
        return tuple(findings), failures

    # ------------------------------------------------------------------

    def _audit(self, report: SweepReport) -> None:
        """One row per sweep, alongside WU-05's row per call."""
        event = {"ts": datetime.now(timezone.utc).isoformat(),
                 "actor": AGENT_NAME, "event": "sweep",
                 "subject_id": report.performer_id}
        event.update(report.as_dict())
        self.deps.audit.append(event)

    def _log(self, report: SweepReport) -> None:
        line = (f"{AGENT_NAME} sweep={report.sweep_id} "
                f"performer={report.performer_id} "
                f"batches={report.batches_run}/{report.batches_planned} "
                f"raw={report.raw_results} findings={len(report.findings)} "
                f"duplicates={report.duplicates_collapsed} "
                f"capped={report.capped} cache_hits={report.from_cache} "
                f"degraded={report.degraded} "
                f"duration_s={report.duration_s:.2f}")
        if report.degraded:
            log.warning("%s reason=%s", line,
                        {"plan": report.plan_degraded,
                         "batches": list(report.batches_degraded),
                         "writes": report.write_failures})
        else:
            log.info(line)
