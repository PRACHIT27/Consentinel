"""WU-26, WU-27 and WU-32 — the audio, video and image sweeps.

One module, three modalities. `DESIGN.md` §2.2 lists AudioSweep, VideoSweep and
ImageSweep separately, and writing them as three files would have produced three
copies of the same six steps differing only in a noun. What actually differs
between them is two things — the words in the query and the content types worth
downloading — so both are parameters.

The chain, per modality:

    Parallel Search  ->  fetch_media  ->  MediaTriage (Gemini)  ->  Reconciler

**The sweep discovers and downloads. It never interprets.** Reading the sample
is `read_media`, which holds no tools and answers a four-field schema — the same
reader the clearance check uses on our own footage, because "what is
perceptible in this file" is one question regardless of who uploaded it. That
keeps the rule intact: every component that touches untrusted content has no
capability. A downloaded sample is untrusted in exactly the way a page is.

**What this cannot do, stated because the temptation is obvious.** Gemini can
hear a voice and see a face. It cannot tell you *whose* they are. So a media
finding never asserts identity: `depicts_named_person` comes from the listing's
own text claiming to sell that performer, and the sample raises or lowers
confidence in the offering being real. A sample alone produces `ambiguous`,
which is correct — a voice-cloning demo reel that names nobody is not evidence
about anybody.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from consentinel.agents.clearance.guardrail import VALIDATORS
from consentinel.agents.clearance.instructions import INSTRUCTION as MEDIA_INSTRUCTION
from consentinel.agents.clearance.instructions import (
    PROMPT_VERSION as MEDIA_PROMPT_VERSION,
)
from consentinel.agents.clearance.instructions import RESPONSE_SCHEMA as MEDIA_SCHEMA
from consentinel.agents.common.gemini import generate_json_about_media
from consentinel.harness.policy import FailState, HarnessPolicy
from consentinel.harness.ports import HarnessDeps
from consentinel.harness.runner import Harness
from consentinel.store.base import Locale, Performer
from consentinel.tools.fetch_media import MediaFetcher, MediaRef, looks_like_media
from consentinel.tools.parallel_search import ParallelSearch

log = logging.getLogger("consentinel.media_sweep")

# What each sweep looks for, and what it will accept when it gets there.
#
# The query words are English here and the planner's locale drives the language
# of the search; these are the *extra* terms that steer a text index towards a
# file rather than a landing page.
MODALITIES: dict[str, dict[str, Any]] = {
    "audio": {
        "terms": ["voice sample mp3", "voice clone demo audio", "tts sample download"],
        "accepts": "audio/",
        "asks_about": "voice",
    },
    "video": {
        "terms": ["deepfake ad video", "synthetic endorsement clip", "ai avatar video sample"],
        "accepts": "video/",
        "asks_about": "performance",
    },
    "image": {
        "terms": ["ai portrait png", "face swap result image", "synthetic headshot"],
        "accepts": "image/",
        "asks_about": "face",
    },
}

# `tools=()` and it matters: this reads a file a stranger published.
READ_POLICY = HarnessPolicy(
    agent_name="media_triage",
    fail_state=FailState.AMBIGUOUS,
    tools=(),
    output_schema=dict,
    temperature=0.0,
    cache="content",          # a sample's bytes never change
    timeout_s=120.0,
)


@dataclass
class MediaCandidate:
    """One downloaded sample and what the model made of it."""

    url: str
    modality: str
    locale: Optional[Locale] = None
    ref: Optional[MediaRef] = None
    perceived_modality: Optional[str] = None
    human_present: Optional[bool] = None
    description: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None       # set when we could not look

    @property
    def ok(self) -> bool:
        return self.ref is not None and self.reason is None


@dataclass
class MediaSweepReport:
    modality: str
    searched: int = 0
    candidates: int = 0
    downloaded: int = 0
    refused: int = 0
    read: int = 0
    with_person: int = 0
    degraded: bool = False
    notes: list[str] = field(default_factory=list)
    results: list[MediaCandidate] = field(default_factory=list)

    def line(self) -> str:
        return (f"{self.modality}: {self.searched} results, {self.candidates} look like "
                f"media, {self.downloaded} downloaded, {self.refused} refused, "
                f"{self.read} read, {self.with_person} with a person in them")


def read_media(ref: MediaRef, *, deps: Optional[HarnessDeps] = None,
               describe: Optional[Any] = None) -> dict[str, Any]:
    """MediaTriage. Ask what is perceptible in one sample; accept four fields.

    Shares its prompt and schema with the clearance inspector on purpose. Two
    prompts asking the same question of the same model would drift, and then
    the inward and outward directions would disagree about what a file
    contains — which is exactly the kind of split this product exists to avoid.
    """
    def call(inst: str, data: bytes, mime: str, schema: dict[str, Any]) -> dict[str, Any]:
        return generate_json_about_media(inst, data, mime, schema,
                                         temperature=READ_POLICY.temperature)

    do_describe = describe or call

    def invoke(repair_hint: Optional[str]) -> dict[str, Any]:
        instruction = MEDIA_INSTRUCTION
        if repair_hint:
            instruction += ("\n\nA previous answer was rejected. Fix this and answer "
                            f"again:\n{repair_hint}")
        return do_describe(instruction, ref.data, ref.mime, MEDIA_SCHEMA)

    harness = Harness(READ_POLICY, deps or HarnessDeps(prompt_version=MEDIA_PROMPT_VERSION))
    result = harness.run(
        invoke,
        subject_id=ref.url,
        cache_key=f"media_triage:{ref.sha256}:{MEDIA_PROMPT_VERSION}",
        validators=VALIDATORS,
    )
    if not result.ok:
        raise RuntimeError(result.reason or "the sample could not be read")
    return result.value or {}


@dataclass
class MediaSweep:
    """Search for samples of one modality, download them, and have them read."""

    modality: str
    deps: HarnessDeps = field(default_factory=HarnessDeps)
    search: Optional[ParallelSearch] = None
    fetcher: MediaFetcher = field(default_factory=MediaFetcher)
    max_downloads: int = 3          # each one is a model call; a sweep is not a crawl
    max_pages: int = 4              # landing pages opened per locale, to bound the crawl
    page_fetcher: Optional[Any] = None
    describe: Optional[Any] = None  # injected in tests

    def __post_init__(self) -> None:
        if self.modality not in MODALITIES:
            raise ValueError(f"modality must be one of {', '.join(MODALITIES)}")
        if self.page_fetcher is None:
            from consentinel.tools.fetch_page import PageFetcher
            from consentinel.tools.web_risk import WebRiskCheck

            self.page_fetcher = PageFetcher(deps=self.deps, risk=WebRiskCheck(deps=self.deps))

    def _media_on(self, page_url: str, subject_id: str, locale) -> list[str]:
        """What one landing page points at. Never raises: a page we cannot open
        contributes nothing, which is different from the sweep failing."""
        if self.page_fetcher is None:
            return []
        try:
            # The locale is not optional here: `fetch_page` reads
            # `locale.language` to set Accept-Language, and passing None makes
            # it fail with an AttributeError that reads like a bug in the page.
            outcome = self.page_fetcher.fetch_detailed(page_url, locale, subject_id=subject_id)
        except Exception as exc:                         # noqa: BLE001
            log.info("could not open %s: %s", page_url, exc)
            return []
        if not outcome.ok or outcome.snapshot is None:
            return []
        return list(outcome.snapshot.media_refs or [])

    def run(self, performer: Performer, locales: Sequence[Locale]) -> MediaSweepReport:
        spec = MODALITIES[self.modality]
        report = MediaSweepReport(modality=self.modality)
        searcher = self.search or ParallelSearch(deps=self.deps)

        for locale in locales:
            queries = [f"{performer.name} {term}" for term in spec["terms"][:2]]
            result = searcher.search(
                objective=(f"Find downloadable {self.modality} samples that claim to "
                           f"reproduce the voice or likeness of {performer.name}."),
                search_queries=queries,
                locale=locale,
                max_results=5,
                subject_id=performer.id,
            )
            if result is None or getattr(result, "degraded", False):
                report.degraded = True
                report.notes.append(f"{locale}: search did not complete")
                continue

            for hit in result.results:
                report.searched += 1
                # A search result is almost never the file itself. The first
                # live run returned five results per query and zero direct
                # media links, because an index returns the page that *sells*
                # the sample, not the sample. So: take the landing page, and
                # harvest the media it points at. `PageSnapshot.media_refs` has
                # been in the frozen contract since the start for this, and
                # `fetch_page` already fills it from img, video, source and
                # srcset.
                direct = looks_like_media(hit.url)
                if direct and spec["accepts"].startswith(direct):
                    report.candidates += 1
                    report.results.append(
                        MediaCandidate(url=hit.url, modality=self.modality, locale=locale))
                    continue

                for media_url in self._media_on(hit.url, performer.id, locale):
                    guess = looks_like_media(media_url)
                    if guess is None or not spec["accepts"].startswith(guess):
                        continue
                    report.candidates += 1
                    report.results.append(
                        MediaCandidate(url=media_url, modality=self.modality,
                                       locale=locale))

        # Download and read, up to the budget. Ordered by how much the address
        # looks like a file, which is all the ordering information we have.
        for candidate in report.results[: self.max_downloads]:
            try:
                candidate.ref = self.fetcher.fetch(candidate.url)
                report.downloaded += 1
            except Exception as exc:                     # noqa: BLE001 - record and continue
                candidate.reason = f"{type(exc).__name__}: {exc}"
                report.refused += 1
                log.info("media refused %s: %s", candidate.url, candidate.reason)
                continue

            try:
                answer = read_media(candidate.ref, deps=self.deps, describe=self.describe)
            except Exception as exc:                     # noqa: BLE001
                candidate.reason = f"the sample could not be read: {exc}"
                log.info("media unread %s: %s", candidate.url, candidate.reason)
                continue

            candidate.perceived_modality = (answer.get("modality") or "").strip().lower()
            candidate.human_present = bool(answer.get("human_present"))
            candidate.description = (answer.get("description") or "").strip() or None
            candidate.confidence = float(answer.get("confidence") or 0.0)
            report.read += 1
            if candidate.human_present:
                report.with_person += 1

        log.info("%s", report.line())
        return report


def corroboration(report: MediaSweepReport) -> float:
    """How much this sweep should raise confidence in a text finding.

    Deliberately small and deliberately capped. A working sample with a person
    in it means the offering is real rather than a parked page, and that is
    *all* it means — it is not identity proof, so it can never carry a verdict
    on its own. `DESIGN.md` §2.2: corroborating evidence, never identity.
    """
    if not report.read:
        return 0.0
    return min(0.15, 0.05 * report.with_person)
