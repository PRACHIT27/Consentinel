"""WU-17 — the clearance check.

The interesting cases are not the happy one. They are all the ways a check can
fail to establish coverage, because every one of those has to land on
`unverified` rather than on `cleared`. A crash read as a pass is the only
outcome this pipeline must never produce.

No network: the model call is injected.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from consentinel.agents.clearance import (
    ACCEPTED_MIME,
    MAX_INLINE_BYTES,
    Declaration,
    check_asset,
    disagrees_with_declaration,
    to_asset,
)
from consentinel.store.base import ClearanceState, Consent, Modality, PermittedUse

BYTES = b"\x00" * 512


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


@pytest.fixture
def grant():
    """Voice only, US and Canada, live until 2028."""
    return Consent(
        id="c1", performer_id="p1", licensee="Halcyon Pictures",
        permitted_uses=[PermittedUse.VOICE_SYNTH],
        territories=["US", "CA"], valid_from=dt(2026, 1, 1), valid_to=dt(2028, 12, 31),
        clause_citations=[{"quote": "Producer may generate synthetic voice performances.",
                           "page": 4}],
    )


@pytest.fixture
def note():
    """A delivery note that matches the grant."""
    return Declaration(
        performer_id="p1", licensee="Halcyon Pictures", modality="voice",
        territories=("US",), vendor="Northlight Post", invoice_ref="NL-2291",
        shot_code="NF_1042_ADR", production_id="prod_x", synthetic="yes",
    )


def heard(modality="voice", human=True, confidence=0.8):
    """A stand-in for the model: it heard what we tell it to have heard."""
    def describe(instruction, data, mime_type, schema):
        return {"modality": modality, "human_present": human,
                "description": "A short line of dialogue.", "confidence": confidence}
    return describe


def run(note, grant, describe=None, **kw):
    return check_asset(
        data=kw.pop("data", BYTES),
        filename=kw.pop("filename", "NF_1042_ADR_v03.wav"),
        mime_type=kw.pop("mime_type", "audio/wav"),
        declaration=note,
        consents=kw.pop("consents", [grant]),
        describe=describe or heard(),
        **kw,
    )


# ------------------------------------------------------------ the happy path


def test_a_covered_clip_is_cleared(note, grant):
    out = run(note, grant, as_of=dt(2026, 9, 9))
    assert out.state == ClearanceState.CLEARED
    assert out.matched_consent_id == "c1", "must name the grant it relied on"
    assert "Halcyon Pictures" in out.reasoning


def test_the_answer_carries_what_the_model_perceived(note, grant):
    """Shown next to the verdict. A clearance answer nobody can interrogate is
    one they have to take on faith."""
    out = run(note, grant, describe=heard(confidence=0.91))
    assert out.perceived_modality == "voice"
    assert out.perceived_description
    assert out.perceived_confidence == pytest.approx(0.91)


# --------------------------------------------- the rules, not the model, decide


def test_a_use_the_contract_withholds_is_blocked(grant):
    """The grant is voice only. A face is not covered, and 'not covered' for our
    own footage means it cannot ship."""
    face = Declaration(performer_id="p1", licensee="Halcyon Pictures", modality="face",
                       territories=("US",), production_id="prod_x")
    out = run(face, grant, describe=heard(modality="face"))
    assert out.state == ClearanceState.BLOCKED
    assert "does not cover" in out.reasoning


def test_a_territory_outside_the_grant_is_blocked(grant):
    """Consent is scoped to countries, so the same clip can be fine in one
    market and not in another."""
    abroad = Declaration(performer_id="p1", licensee="Halcyon Pictures", modality="voice",
                         territories=("BR",), production_id="prod_x")
    out = run(abroad, grant)
    assert out.state == ClearanceState.BLOCKED
    assert "BR" in out.reasoning


def test_an_expired_grant_does_not_clear_anything(note, grant):
    out = run(note, grant, as_of=dt(2029, 1, 1))
    assert out.state == ClearanceState.BLOCKED
    assert "expired" in out.reasoning


def test_a_different_studio_shipping_it_is_not_covered(grant):
    """The grant is held by Halcyon. Another studio holding no grant is in the
    same position as a stranger, which is the point of one rule engine."""
    other = Declaration(performer_id="p1", licensee="Cyan Alley VFX", modality="voice",
                        territories=("US",), production_id="prod_x")
    out = run(other, grant)
    assert out.state != ClearanceState.CLEARED
    assert "Cyan Alley VFX" in out.reasoning


def test_a_declared_synthetic_with_no_grant_on_file_is_blocked(note):
    """Not `unverified`, and the difference matters.

    `unverified` is for a clip whose facts we could not establish. Here we
    established them — the production told us this is a synthetic voice of a
    named performer for a named market — and the registry holds nothing that
    permits it. The same engine calls that `unauthorized` when a stranger does
    it, and a studio should not get a softer answer about itself.
    """
    out = run(note, None, consents=[])
    assert out.state == ClearanceState.BLOCKED
    assert out.check == "no_grant"


# ------------------------------------------------ every failure lands on doubt


def test_a_file_we_cannot_read_is_refused_before_a_model_call(note, grant):
    """And it is refused with a sentence a person can act on, not an API error."""
    def explode(*args, **kwargs):
        raise AssertionError("must not spend a model call on an unreadable type")

    out = run(note, grant, describe=explode, mime_type="text/plain")
    assert out.state == ClearanceState.UNVERIFIED
    assert "audio, video and images only" in out.reasoning


def test_an_empty_upload_is_refused(note, grant):
    out = run(note, grant, data=b"")
    assert out.state == ClearanceState.UNVERIFIED


def test_a_file_too_big_to_read_inline_is_refused(note, grant):
    out = run(note, grant, data=b"\x00" * (MAX_INLINE_BYTES + 1))
    assert out.state == ClearanceState.UNVERIFIED
    assert "shorter excerpt" in out.reasoning


def test_a_model_failure_does_not_clear_on_the_declaration_alone(note, grant):
    """The declaration would have cleared this clip. That is exactly why a
    failed read must not: clearing a file nobody managed to open would make the
    check theatre."""
    def broken(*args, **kwargs):
        raise RuntimeError("vertex said no")

    out = run(note, grant, describe=broken)
    assert out.state == ClearanceState.UNVERIFIED
    assert "could not read the file" in out.reasoning


def test_a_file_with_nobody_in_it_is_not_cleared(note, grant):
    out = run(note, grant, describe=heard(human=False))
    assert out.state == ClearanceState.UNVERIFIED
    assert "could not perceive a person" in out.reasoning


def test_paperwork_disagreeing_with_the_file_withholds_clearance(note, grant):
    """The note says voice, the file shows a face. Clearing it on either reading
    would be signing off a document we have reason to doubt."""
    out = run(note, grant, describe=heard(modality="face"))
    assert out.state == ClearanceState.UNVERIFIED
    assert "disagree" in out.reasoning


def test_an_answer_outside_our_vocabulary_is_rejected(note, grant):
    out = run(note, grant, describe=heard(modality="vibes"))
    assert out.state == ClearanceState.UNVERIFIED


def test_a_declaration_we_do_not_understand_cannot_clear(grant):
    nonsense = Declaration(performer_id="p1", licensee="Halcyon Pictures",
                           modality="hologram", territories=("US",), production_id="prod_x")
    out = run(nonsense, grant, describe=heard(modality="none"))
    assert out.state == ClearanceState.UNVERIFIED


# -------------------------------------------------------- the disagreement rule


@pytest.mark.parametrize("perceived,declared,conflict", [
    ("voice", "voice", False),
    ("face", "voice", True),
    ("voice", "performance", False),   # a performance contains a voice
    ("face", "performance", False),
    ("none", "voice", False),          # handled as its own case, not as conflict
    ("", "voice", False),
])
def test_when_the_note_and_the_file_are_taken_to_disagree(perceived, declared, conflict):
    assert bool(disagrees_with_declaration(perceived, declared)) is conflict


# ------------------------------------------------------------------- the row


def test_the_stored_row_cannot_claim_a_state_the_check_did_not_produce(note, grant):
    out = run(note, grant)
    asset = to_asset(out, note, asset_id="asset_1", filename="clip.wav")
    assert asset.clearance_state == out.state
    assert asset.matched_consent_id == out.matched_consent_id
    assert asset.detected_modality == Modality.VOICE
    assert asset.content_hash == out.content_hash
    assert asset.provenance_metadata["declaration"].startswith("Northlight Post delivered")


def test_the_same_file_hashes_the_same_way(note, grant):
    """The row id is built from this, so re-checking a clip updates it instead of
    adding a second row - which is what makes the demo honest: check it, watch
    it blocked, add the slip, check again."""
    first = run(note, grant)
    second = run(note, grant)
    assert first.content_hash == second.content_hash


def test_wav_and_mp4_are_both_accepted():
    assert "audio/wav" in ACCEPTED_MIME and "video/mp4" in ACCEPTED_MIME
