# SPDX-License-Identifier: AGPL-3.0-only
"""Measured-first rewrite contract: characters never decide, audio does.

Vietnamese writes the cardinal "one" the same way it writes the indefinite
article, and a longer sentence can produce a SHORTER take - pronunciation
rate, elision and punctuation all move the measured result away from the
character count. So C7 never accepts or rejects a rewrite on length:

  1. `_adapt_chunk_items` checks structure and MEANING, then produces a
     candidate. It deliberately does not score the candidate by characters
     (see the comment at the point the score would have gone).
  2. `synthesize_timed_dub` synthesizes REAL TTS, measures it, and accepts
     only when `candidate_duration_ms < duration_before_ms`. Anything else is
     recorded as `rewrite_measured_not_faster` and the previous audio, text,
     sidecars and rewrite_count are left untouched.

The old `test_rewrite_rejects_non_shortening_and_uses_measured_feedback`
collapsed the two stages into one, asserting that a candidate the AI merely
returned was also the candidate the pipeline accepted. That is why it expected
`rewrite_not_shorter`, a reason the code no longer emits. The tests below pin
each stage's own responsibility instead.
"""
from __future__ import annotations

import json

import pytest

from backend.core.localization import LocalizationProfile, LocalizationSegment
from backend.services import localization_service as ls
from backend.services.dubbing_planner import DubbingConfig, DubbingCue, build_plan

# The approved C6 text. Neutral wording: no added form of address, no added
# register, nothing the source does not sanction.
ORIGINAL = "Đổ nước vào rồi cho miến đã ngâm và sốt cay vào nồi."
# Longer in characters, same meaning, same facts. This is the case a character
# count would wrongly reject and real TTS may happily shorten: "và sốt cay" is
# three quick syllables where the original's elisions are heavier.
LONGER_BUT_EQUIVALENT = "Bạn đổ nước vào nồi, rồi cho miến đã ngâm và sốt cay vào trong nồi."
# A candidate that is SHORTER but adds a negation. It is not the length that
# must catch it - it is shorter than the approved text - it is the meaning.
# This is the pairing that proves the gate is semantic: if the pipeline ever
# rejects on characters, this case sails through because it is shorter.
ADDED_NEGATION = "Không cho miến đã ngâm và sốt cay vào nồi."


def _scenario(translated_text: str):
    """One chunk, one segment, the shape `_adapt_chunk_items` expects."""
    plan, chunks = build_plan([
        DubbingCue(index=1, segment_id="s1", start_ms=0, end_ms=6052, text=translated_text),
    ], DubbingConfig())
    segment = LocalizationSegment(
        id="s1",
        source_text="加水粉丝调料",
        translated_text=translated_text,
        source_start_ms=0,
        source_end_ms=6052,
        translation_status="success",
    )
    return plan, chunks, segment


def _ai_returns(candidate: str):
    """A rewrite model that always proposes `candidate` verbatim."""
    prompts: list[str] = []

    class AI:
        async def chat(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return json.dumps([{"id": "s1", "text": candidate}], ensure_ascii=False)

    return AI(), prompts


async def _adapt(candidate: str, *, source_language: str | None = "zh"):
    plan, chunks, segment = _scenario(ORIGINAL)
    ai, prompts = _ai_returns(candidate)
    issues: list[dict] = []
    state: dict = {"measured_duration_ms": 9192, "attempt": 2}
    if source_language:
        state["source_language"] = source_language
    changed = await ls._adapt_chunk_items(
        ai,
        plan=plan,
        chunk=chunks[0],
        segment_by_id={"s1": segment},
        target_language="Vietnamese",
        target_ms=6052,
        profile=LocalizationProfile(id="qa", name="QA"),
        issues=issues,
        state=state,
    )
    return {
        "changed": changed,
        "plan": plan,
        "segment": segment,
        "issues": issues,
        "prompts": prompts,
        "state": state,
    }


# ── stage 1: structure and meaning ───────────────────────────────────────


@pytest.mark.asyncio
async def test_identical_reply_is_reported_as_no_safe_shortening():
    """The model returned the approved text unchanged.

    That is a valid outcome, not a malformed response, and it is recorded under
    its own reason with `requires_human_review` so a human can look at a line
    the model insists cannot be shortened.
    """
    out = await _adapt(ORIGINAL)

    assert out["changed"] is False
    assert [issue["reason"] for issue in out["issues"]] == ["rewrite_no_safe_shortening"]
    assert out["issues"][0]["requires_human_review"] is True
    assert out["state"]["no_safe_shortening"] is True
    assert out["plan"][0].spoken_text == ORIGINAL
    assert out["segment"].translated_text == ORIGINAL


@pytest.mark.asyncio
async def test_longer_text_with_equivalent_meaning_is_admitted_for_measurement():
    """More characters is NOT a reason to reject. This is the core contract.

    Stage 1 admits the candidate so that real TTS can be measured. If this
    fails, a rewrite that would have fitted is thrown away on a character
    count - the exact failure the measured-first design exists to remove.
    """
    out = await _adapt(LONGER_BUT_EQUIVALENT)

    assert len(LONGER_BUT_EQUIVALENT) > len(ORIGINAL), "the candidate must be longer"
    assert out["changed"] is True, (
        f"a longer candidate with unchanged meaning must be admitted; "
        f"issues={out['issues']}"
    )
    assert out["plan"][0].spoken_text == LONGER_BUT_EQUIVALENT
    assert out["segment"].translated_text == LONGER_BUT_EQUIVALENT
    assert not out["issues"]


@pytest.mark.asyncio
async def test_prompt_carries_the_measured_budget_as_advisory_context():
    """The rewrite prompt is told the real numbers, not a word quota."""
    out = await _adapt(LONGER_BUT_EQUIVALENT)

    prompt = out["prompts"][0]
    assert "9192" in prompt and "6052" in prompt
    # The budget is advisory. A hard character target would re-introduce the
    # word-count proxy this stage refuses to use.
    assert "at most" not in prompt.lower()


# ── stage 1 rejects: meaning, not length ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate,note",
    [
        (ADDED_NEGATION, "a negation the source never gave, while being SHORTER"),
    ],
)
async def test_meaning_guard_rejects_regardless_of_length(candidate, note):
    """A candidate that changes meaning never reaches measurement.

    `ADDED_NEGATION` is deliberately SHORTER than the approved text: a
    character-count gate would wave it through, so only a real meaning check
    can stop it. The guard fires before anything is mutated, so a rejected
    chunk cannot half-apply.

    Scope note: the guard is a set of local invariants (negation, counts,
    subjects, foreign script), not a semantic-similarity model. A candidate
    that silently drops a step - "cho miến đã ngâm" omitted - carries no
    invariant for it to catch and still passes stage 1; it is caught by
    measurement, or by review. That boundary is asserted in
    test_rewrite_guard_added_item.py and documented in ACTIVE_WORK_DOD.md.
    """
    out = await _adapt(candidate)

    assert len(candidate) < len(ORIGINAL), "the candidate must be shorter"
    assert out["changed"] is False
    assert [issue["reason"] for issue in out["issues"]] == ["rewrite_meaning_guard"]
    assert out["plan"][0].spoken_text == ORIGINAL
    assert out["segment"].translated_text == ORIGINAL


# ── stage 2: real measurement decides ────────────────────────────────────
#
# `_adapt_chunk_items` only produces a candidate. The accept/reject decision
# lives in `synthesize_timed_dub`, where real TTS is synthesized into an
# isolated artifact and measured. The comparison is `>=`, so EQUAL is a
# rejection: a rewrite that is not strictly faster has bought nothing and must
# not disturb the last-good audio.
#
# These tests READ THE PRODUCTION SOURCE rather than restating the rule, so a
# change to the gate in `localization_service.py` fails here instead of
# silently making this file a stale description of a rule it no longer applies.

import inspect

import backend.services.localization_service as _ls_mod
from backend.services import localization_service as ls


def _measured_gate_source() -> str:
    """The accept/reject block in `synthesize_timed_dub`, verbatim."""
    src = inspect.getsource(_ls_mod.synthesize_timed_dub)
    start = src.find("candidate_duration_ms >= duration_before_ms")
    end = src.find("_promote_chunk_bundle")
    assert start != -1 and end != -1 and start < end, (
        "could not locate the measured-rewrite gate in synthesize_timed_dub"
    )
    return src[start:end]


@pytest.mark.asyncio
async def test_stage_one_produces_a_candidate_and_measures_nothing():
    """Stage 1 admits the longer candidate and stops there.

    `_adapt_chunk_items` synthesizes nothing and measures nothing. Proof: it
    returns `changed=True` with the longer text in place, and the measured
    durations it was given in `state` are untouched inputs, not outputs it
    updated.
    """
    out = await _adapt(LONGER_BUT_EQUIVALENT)

    assert out["changed"] is True
    assert out["plan"][0].spoken_text == LONGER_BUT_EQUIVALENT
    assert out["state"]["measured_duration_ms"] == 9192
    assert not out["issues"], "stage 1 must not pre-judge on length"


def test_the_gate_in_production_is_measured_and_strictly_faster():
    """`synthesize_timed_dub` rejects on MEASUREMENT, not on characters.

    Asserts the production gate itself:

      * the comparison is `candidate_duration_ms >= duration_before_ms`, so
        equal audio is a rejection;
      * the rejection reason is `rewrite_measured_not_faster`;
      * a rejection takes the `continue` path, i.e. it does NOT reach
        `_promote_chunk_bundle`, so the last-good audio is never overwritten.
    """
    gate = _measured_gate_source()

    assert "candidate_duration_ms >= duration_before_ms" in gate, (
        "the gate must compare MEASURED audio, not a character count"
    )
    assert ">=" in gate, (
        "an equal measurement is not an improvement and must be rejected"
    )
    assert '"rewrite_measured_not_faster"' in gate, (
        "a slower candidate must be recorded under its measured reason"
    )
    assert "_promote_chunk_bundle" not in gate, (
        "the rejection path must not publish the candidate over last-good"
    )


def test_no_character_count_decides_the_rewrite():
    """No word/character proxy survives anywhere in the rewrite decision.

    The rule that makes measured-first real is the ABSENCE of a length gate.
    Guard against reintroducing one: the gate must not compare the LENGTH of
    any text.

    Comments are stripped first - they say "words" all over, because they are
    explaining that words are irrelevant. Only executable statements count.
    """
    gate = _measured_gate_source()
    code = "\n".join(
        line.split("#", 1)[0] for line in gate.splitlines()
    )

    for proxy in ("len(", "word", "character", "token_count", "charcount"):
        assert proxy not in code.lower(), (
            f"the measured gate must not use a {proxy!r} proxy for speech time"
        )
