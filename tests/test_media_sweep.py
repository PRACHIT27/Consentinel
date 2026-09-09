"""WU-26, 27, 28, 32 — the media sweeps and the fetcher underneath them.

Two things are worth testing here and the rest is covered next door.

The **fetcher** is a network guard, so its tests are about what it refuses: a
private address, a page pretending to be a sample, a file larger than a model
can read. `validate_url` itself is `fetch_page`'s and has its own tests — this
asserts we actually call it.

The **sweep** is about where samples come from. The first live run found zero,
because a search index returns the page that sells a sample, not the sample.
Reading the landing page and harvesting what it points at is the fix, and the
test that would have caught the original mistake is the one asserting a
non-media search result still yields candidates.

No network anywhere: search, fetch and read are all injected.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace as NS

import pytest

from consentinel.agents.media_sweep import MODALITIES, MediaSweep, corroboration
from consentinel.store.base import Locale, Performer
from consentinel.tools.fetch_media import MediaFetcher, MediaRef, looks_like_media
from consentinel.tools.fetch_page import BlockedAddress

MIRA = Performer(id="perf_mira", name="Mira Vance", aliases=["M. Vance"])
EN_US = Locale(language="en", region="US")


# ------------------------------------------------------------- the fetcher


def test_a_private_address_is_refused_by_fetch_pages_guard():
    """Not re-implemented here. A second SSRF implementation is a second SSRF
    bug, so this asserts the shared guard is reached at all."""
    with pytest.raises(BlockedAddress):
        MediaFetcher().fetch("http://169.254.169.254/latest/meta-data/")


def test_a_scheme_we_do_not_speak_is_refused():
    with pytest.raises(BlockedAddress):
        MediaFetcher().fetch("file:///etc/passwd")


@pytest.mark.parametrize("url,kind", [
    ("https://x.test/a/sample.mp3", "audio"),
    ("https://x.test/a/clip.MP4?token=1", "video"),
    ("https://x.test/a/still.webp", "image"),
    ("https://x.test/a/landing-page", None),
    ("https://x.test/watch?v=abc", None),
])
def test_the_address_guess_is_only_a_guess(url, kind):
    assert looks_like_media(url) is kind


# --------------------------------------------------------------- the sweep


def _sweep(modality, *, results, page_media, downloads=None, **kw):
    """A sweep with its search, page fetch, download and read all stubbed."""
    hits = NS(results=[NS(url=u) for u in results], degraded=False)

    def fetch(url):
        if downloads is not None and url not in downloads:
            raise RuntimeError(f"nothing at {url}")
        data = b"\x00" * 64
        return MediaRef(url=url, mime=f"{modality}/x", sha256=hashlib.sha256(data).hexdigest(),
                        size=len(data), data=data)

    return MediaSweep(
        modality=modality,
        search=NS(search=lambda **_: hits),
        page_fetcher=NS(fetch_detailed=lambda url, locale, **_: NS(
            ok=True, snapshot=NS(media_refs=page_media))),
        fetcher=NS(fetch=fetch),
        describe=lambda *a: {"modality": MODALITIES[modality]["asks_about"],
                             "human_present": True,
                             "description": "A person is present.", "confidence": 0.6},
        **kw,
    )


def test_a_search_result_that_is_not_a_file_still_yields_samples():
    """The mistake this catches shipped once. A search index returns the page
    that *sells* the sample, so demanding a direct `.mp3` link found nothing on
    the first live run: five results, zero candidates."""
    sweep = _sweep("image",
                   results=["https://seller.test/product/face-swap"],
                   page_media=["https://cdn.test/a/preview.png",
                               "https://cdn.test/a/logo.webp"])
    report = sweep.run(MIRA, [EN_US])

    assert report.searched == 1
    assert report.candidates == 2, "both media links on the landing page"
    assert report.downloaded == 2 and report.read == 2


def test_a_direct_media_link_skips_the_landing_page():
    sweep = _sweep("audio",
                   results=["https://cdn.test/sample.mp3"],
                   page_media=["https://cdn.test/should-not-be-used.mp3"])
    report = sweep.run(MIRA, [EN_US])

    assert [c.url for c in report.results] == ["https://cdn.test/sample.mp3"]


def test_media_of_the_wrong_modality_is_ignored():
    """An audio sweep that downloads the page's hero image has learned nothing
    about whether a voice clone is real."""
    sweep = _sweep("audio",
                   results=["https://seller.test/voices"],
                   page_media=["https://cdn.test/hero.png", "https://cdn.test/demo.mp3"])
    report = sweep.run(MIRA, [EN_US])

    assert [c.url for c in report.results] == ["https://cdn.test/demo.mp3"]


def test_downloads_are_capped_because_each_one_is_a_model_call():
    sweep = _sweep("image",
                   results=["https://seller.test/x"],
                   page_media=[f"https://cdn.test/{i}.png" for i in range(10)],
                   max_downloads=2)
    report = sweep.run(MIRA, [EN_US])

    assert report.candidates == 10
    assert report.downloaded == 2, "found ten, opened two"


def test_a_sample_we_cannot_download_is_recorded_not_dropped():
    sweep = _sweep("image",
                   results=["https://seller.test/x"],
                   page_media=["https://cdn.test/gone.png"],
                   downloads=set())
    report = sweep.run(MIRA, [EN_US])

    assert report.refused == 1 and report.read == 0
    assert "nothing at" in report.results[0].reason


def test_a_page_we_cannot_open_contributes_nothing_and_does_not_fail_the_sweep():
    """Different from the sweep failing. One dead landing page out of five is
    a normal afternoon on the web."""
    sweep = MediaSweep(
        modality="image",
        search=NS(search=lambda **_: NS(results=[NS(url="https://dead.test/x")], degraded=False)),
        page_fetcher=NS(fetch_detailed=lambda *a, **k: NS(ok=False, snapshot=None)),
    )
    report = sweep.run(MIRA, [EN_US])

    assert report.searched == 1 and report.candidates == 0
    assert report.degraded is False


def test_corroboration_is_small_and_capped():
    """A working sample means the offering is real, and that is all it means.
    It is never identity proof, so it can never carry a verdict on its own."""
    sweep = _sweep("image",
                   results=["https://seller.test/x"],
                   page_media=[f"https://cdn.test/{i}.png" for i in range(6)],
                   max_downloads=6)
    report = sweep.run(MIRA, [EN_US])

    assert report.with_person == 6
    assert corroboration(report) == 0.15, "capped however many samples we find"
    assert corroboration(NS(read=0, with_person=0)) == 0.0


def test_an_unknown_modality_is_refused_at_construction():
    with pytest.raises(ValueError):
        MediaSweep(modality="telepathy")
