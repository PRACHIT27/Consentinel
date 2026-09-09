"""WU-02 seed loader.

Parsing is tested offline. The round-trip test runs against the real project
under a throwaway collection prefix and cleans up after itself, same as the
store tests.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest

from consentinel.seed import load_fixture, parse, reset, seed
from consentinel.store.base import (
    ClearanceState,
    DiscoveredVia,
    FindingStatus,
    PermittedUse,
    Verdict,
)

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
needs_gcp = pytest.mark.skipif(not PROJECT, reason="GOOGLE_CLOUD_PROJECT not set")


@pytest.fixture(scope="module")
def records():
    return parse(load_fixture())


# ------------------------------------------------------------------ parsing


def test_fixture_shape(records):
    assert len(records["performers"]) == 1
    assert len(records["consents"]) == 1
    assert len(records["findings"]) == 4
    assert len(records["assets"]) == 3


def test_enums_are_decoded_not_left_as_strings(records):
    consent = records["consents"][0]
    assert PermittedUse.VOICE_SYNTH in consent.permitted_uses
    assert all(isinstance(u, PermittedUse) for u in consent.permitted_uses)

    finding = records["findings"][0]
    assert isinstance(finding.verdict, Verdict)
    assert isinstance(finding.status, FindingStatus)
    assert isinstance(finding.discovered_via, DiscoveredVia)

    assert isinstance(records["assets"][0].clearance_state, ClearanceState)


def test_timestamps_are_datetimes(records):
    assert isinstance(records["consents"][0].valid_from, datetime)
    assert isinstance(records["findings"][0].first_seen, datetime)


def test_unknown_fixture_keys_are_ignored():
    """The fixture carries a `_comment`; parsing must not choke on it."""
    raw = load_fixture()
    assert "_comment" in raw
    parse(raw)  # would raise if unknown keys reached the constructor


def test_territory_scoped_finding_is_present(records):
    """The pt-BR finding is the one that proves territory scoping in the demo,
    so a fixture edit that drops it should fail here rather than surface as a
    flat demo."""
    br = [f for f in records["findings"] if "BR" in f.target_territories]
    assert br, "fixture lost its territory-scoped finding"
    assert br[0].verdict is Verdict.UNAUTHORIZED
    assert br[0].discovered_locale == "pt-BR"


def test_clearance_fixture_covers_all_three_states(records):
    states = {a.clearance_state for a in records["assets"]}
    assert states == {
        ClearanceState.CLEARED,
        ClearanceState.BLOCKED,
        ClearanceState.UNVERIFIED,
    }


# ------------------------------------------------------------ round trip


@needs_gcp
def test_seed_is_idempotent(records):
    from consentinel.store.firestore_store import FirestoreStore

    prefix = f"test_seed_{uuid.uuid4().hex[:8]}_"
    store = FirestoreStore(project=PROJECT, prefix=prefix)
    try:
        seed(store, records)
        first = len(store.list_findings())
        assert first == 4
        assert len(store.list_performers()) == 1

        seed(store, records)  # again
        assert len(store.list_findings()) == first, "second seed duplicated rows"

        # the seeded grant is readable and still carries its clause citations
        performer = store.list_performers()[0]
        consents = store.list_consents(performer.id)
        assert consents and consents[0].clause_citations
    finally:
        store.purge()


@needs_gcp
def test_reset_removes_only_the_fixture(records):
    from consentinel.store.firestore_store import FirestoreStore

    prefix = f"test_seed_{uuid.uuid4().hex[:8]}_"
    store = FirestoreStore(project=PROJECT, prefix=prefix)
    try:
        seed(store, records)
        # a document the fixture does not define must survive a reset.
        # parse afresh rather than mutating the module-scoped fixture.
        keeper = parse(load_fixture())["performers"][0]
        keeper.id = "perf_not_in_fixture"
        store.upsert_performer(keeper)

        reset(store, parse(load_fixture()))
        remaining = {p.id for p in store.list_performers()}
        assert remaining == {"perf_not_in_fixture"}
        assert store.list_findings() == []
    finally:
        store.purge()
