"""WU-19 — the cache.

Key construction is tested offline, because that is where the two rules live
that quietly corrupt answers when they are missed. The round-trip tests run
against the real project under a throwaway prefix and clean up after themselves.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from consentinel.cache import (
    INLINE_LIMIT_BYTES,
    TTL_SEARCH,
    FirestoreCache,
    content_key,
    normalise_url,
    web_key,
)

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
needs_gcp = pytest.mark.skipif(not PROJECT, reason="GOOGLE_CLOUD_PROJECT not set")


@pytest.fixture
def cache():
    c = FirestoreCache(project=PROJECT, prefix=f"test_cache_{uuid.uuid4().hex[:8]}_")
    try:
        yield c
    finally:
        c.purge()


# ------------------------------------------------------------------- the keys


def test_the_same_bytes_always_give_the_same_key():
    """Content-addressed. This is what makes re-running a contract free."""
    assert content_key("consent", b"same", prompt_version="v1") == \
           content_key("consent", b"same", prompt_version="v1")


def test_a_new_prompt_version_misses_the_cache():
    """Without this you tune a prompt, the old answers keep coming back, and you
    debug output produced by wording you already deleted."""
    assert content_key("consent", b"x", prompt_version="v1") != \
           content_key("consent", b"x", prompt_version="v2")


def test_a_different_locale_misses_the_cache():
    """The one that corrupts answers rather than breaking them. Serve a cached
    US result for a Brazilian query and the territory logic is quietly wrong."""
    assert web_key("search", "mira vance voice", locale="en-US") != \
           web_key("search", "mira vance voice", locale="pt-BR")


def test_urls_that_fetch_the_same_page_share_a_key():
    """Fragments never reach the server and query order does not matter. Without
    this the same page arrives three times as three findings."""
    a = normalise_url("https://Example.INVALID/x?b=2&a=1#section")
    b = normalise_url("https://example.invalid/x?a=1&b=2")
    assert a == b
    assert web_key("page", a) == web_key("page", b)


def test_a_different_page_does_not_share_a_key():
    assert normalise_url("https://x.invalid/a") != normalise_url("https://x.invalid/b")


# -------------------------------------------------------------- round trip


@needs_gcp
def test_a_value_comes_back_as_it_went_in(cache):
    cache.put("k1", {"results": [1, 2, 3], "note": "hello"}, ttl_seconds=TTL_SEARCH)
    hit = cache.get("k1")
    assert hit is not None
    assert hit.value == {"results": [1, 2, 3], "note": "hello"}
    assert hit.age_seconds() < 60


@needs_gcp
def test_a_miss_is_a_miss_not_an_error(cache):
    assert cache.get("never-written") is None


@needs_gcp
def test_an_entry_with_no_ttl_never_expires(cache):
    """Content-addressed entries are stored without an expiry, because the key
    already guarantees the answer cannot change."""
    cache.put("forever", {"a": 1})           # no ttl_seconds
    doc = cache._col().document(cache._doc_id("forever")).get().to_dict()
    assert doc["expires_at"] is None, "a content-addressed entry must have no expiry"
    assert cache.get("forever").value == {"a": 1}


@needs_gcp
def test_an_expired_entry_is_treated_as_gone(cache):
    """Firestore's TTL sweep is not instant, so an entry can still be present
    after its time. Serving it would be stale data — the sweep is housekeeping,
    not correctness."""
    cache.put("stale", {"old": True}, ttl_seconds=60)
    ref = cache._col().document(cache._doc_id("stale"))
    ref.update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)})

    assert cache.get("stale") is None
    assert cache.stats["expired"] == 1
    assert ref.get().exists, "the document is still there; we just refused to serve it"


@needs_gcp
def test_something_too_big_for_a_document_goes_to_storage(cache):
    """Firestore documents cap at 1 MiB and page text gets close, so large
    payloads go to Cloud Storage and the document keeps only a pointer."""
    big = "x" * (INLINE_LIMIT_BYTES + 5000)
    cache.put("bigpage", {"text": big}, ttl_seconds=TTL_SEARCH)

    doc = cache._col().document(cache._doc_id("bigpage")).get().to_dict()
    assert "blob" in doc and doc.get("inline") is None
    assert cache.stats["blobs"] == 1
    assert cache.get("bigpage").value["text"] == big


@needs_gcp
def test_a_missing_blob_misses_rather_than_raising(cache):
    """A cache that raises is worse than a cache that misses — the caller can
    always recompute."""
    big = "y" * (INLINE_LIMIT_BYTES + 1000)
    cache.put("gone", {"text": big}, ttl_seconds=TTL_SEARCH)
    cache._col().document(cache._doc_id("gone")).update({"blob": "cache/does-not-exist"})

    assert cache.get("gone") is None


@needs_gcp
def test_objects_survive_the_round_trip(cache):
    """JSON where possible so entries stay readable in the console, pickle when
    the value is not JSON."""
    from consentinel.store.base import Locale

    cache.put("obj", Locale("pt", "BR"))
    assert cache.get("obj").value == Locale("pt", "BR")


@needs_gcp
def test_purge_refuses_without_a_prefix():
    """An unprefixed purge would empty the live cache mid-demo."""
    live = FirestoreCache(project=PROJECT)
    with pytest.raises(RuntimeError, match="prefix"):
        live.purge()


@needs_gcp
def test_the_harness_can_use_it_directly(cache):
    """It has to satisfy the harness's CachePort, or step 3 does nothing."""
    from consentinel.harness.policy import FailState, HarnessPolicy
    from consentinel.harness.ports import HarnessDeps
    from consentinel.harness.runner import Harness

    policy = HarnessPolicy(agent_name="demo", fail_state=FailState.AMBIGUOUS,
                           output_schema=dict, cache="content")
    h = Harness(policy, HarnessDeps(cache=cache, prompt_version="v1"))

    calls = {"n": 0}

    def work(hint):
        calls["n"] += 1
        return {"answer": 42}

    first = h.run(work, cache_key="demo:1")
    second = h.run(work, cache_key="demo:1")

    assert first.value == second.value == {"answer": 42}
    assert calls["n"] == 1, "the second run should have been served from cache"
    assert second.from_cache is True
    assert second.cache_age_s is not None
