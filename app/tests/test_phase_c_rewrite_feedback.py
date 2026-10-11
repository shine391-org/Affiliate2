# SPDX-License-Identifier: AGPL-3.0-only
"""Duration-feedback regressions from real Gemma/Edge dubbing runs."""
import json

import pytest

from backend.core.localization import LocalizationProfile, LocalizationSegment
from backend.services import localization_service as ls
from backend.services.dubbing_planner import DubbingConfig, DubbingCue, build_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [
    "Đổ nước vào rồi cho miến đã ngâm và sốt cay vào nồi. nhé bạn ơi.",
    "Đổ nước vào rồi cho miến đã ngâm và sốt cay vào nồi.",
])
async def test_identical_reply_is_recorded_as_no_safe_shortening(candidate):
    """Stage 1 produces a candidate; only measured TTS can accept or reject it.

    This test used to be named `..._rejects_non_shortening_...` and expected a
    `rewrite_not_shorter` reason. That collapsed two stages into one: it treated
    the text the AI returned as the rewrite the pipeline accepts. It is not.

    Stage 1 (`_adapt_chunk_items`) validates structure and meaning and hands a
    candidate on. It deliberately does NOT score by characters - Vietnamese
    pronunciation, elision and punctuation make a longer sentence capable of a
    shorter take. Stage 2 (`synthesize_timed_dub`) synthesizes real TTS,
    measures it, and accepts only when `candidate_duration_ms <
    duration_before_ms`.

    So what is asserted here is stage 1's own responsibility: a structurally
    valid candidate is admitted with no length-based complaint, because the
    accept/reject decision is not this stage's to make. The measured side of
    that contract lives in test_phase_c_rewrite_measured_first.py.

    The longer candidate carries an added form of address ("nhé bạn ơi"). It is
    kept deliberately: passing stage 1 must not be read as stage 1 approving
    it, which is exactly the confusion the old name created.
    """
    original = "Đổ nước vào rồi cho miến đã ngâm và sốt cay vào nồi."
    plan, chunks = build_plan([
        DubbingCue(index=1, segment_id="s1", start_ms=0, end_ms=6052, text=original),
    ], DubbingConfig())
    segment = LocalizationSegment(
        id="s1", source_text="加水粉丝调料", translated_text=original,
        source_start_ms=0, source_end_ms=6052, translation_status="success",
    )
    prompts = []

    class AI:
        async def chat(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return json.dumps([{"id": "s1", "text": candidate}])

    issues = []
    changed = await ls._adapt_chunk_items(
        AI(), plan=plan, chunk=chunks[0], segment_by_id={"s1": segment},
        target_language="Vietnamese", target_ms=6052,
        profile=LocalizationProfile(id="qa", name="QA"), issues=issues,
        state={"measured_duration_ms": 9192, "attempt": 2},
    )
    # Two distinct outcomes, both correct, and the distinction is the point:
    # identical text means the model found nothing to shorten; a different
    # candidate is admitted as a candidate. Admitted is not accepted - that
    # needs measurement, which this test does not perform.
    if candidate == original:
        assert changed is False
        assert [issue["reason"] for issue in issues] == ["rewrite_no_safe_shortening"]
    else:
        assert changed is True, (
            "stage 1 must admit a structurally valid candidate; the "
            "accept/reject decision belongs to measurement, not to a character "
            "count"
        )
        assert not issues, (
            f"stage 1 raised no reason for a candidate that differs but is not "
            f"malformed; got {issues}"
        )
    assert plan[0].spoken_text == candidate
    assert segment.translated_text == candidate
    assert "9192" in prompts[0]
    assert "6052" in prompts[0]
    assert "word budget" in prompts[0].lower()


@pytest.mark.asyncio
async def test_measured_feedback_is_carried_in_the_prompt():
    """The rewrite prompt receives the real measured numbers as context.

    The model is told what the audio actually measured (9192ms) against the
    budget it has to fit (6052ms). That is context, not a quota - the same
    prompt must not harden into a character target, or measured-first is
    undone at the prompt layer.
    """
    original = "Đổ nước vào rồi cho miến đã ngâm và sốt cay vào nồi."
    plan, chunks = build_plan([
        DubbingCue(index=1, segment_id="s1", start_ms=0, end_ms=6052, text=original),
    ], DubbingConfig())
    segment = LocalizationSegment(
        id="s1", source_text="加水粉丝调料", translated_text=original,
        source_start_ms=0, source_end_ms=6052, translation_status="success",
    )
    prompts = []

    class AI:
        async def chat(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return json.dumps([{"id": "s1", "text": original}], ensure_ascii=False)

    await ls._adapt_chunk_items(
        AI(), plan=plan, chunk=chunks[0], segment_by_id={"s1": segment},
        target_language="Vietnamese", target_ms=6052,
        profile=LocalizationProfile(id="qa", name="QA"), issues=[],
        state={"measured_duration_ms": 9192, "attempt": 2},
    )
    prompt = prompts[0]
    assert "9192" in prompt and "6052" in prompt
    assert "word budget" in prompt.lower()
    assert "at most" not in prompt.lower(), (
        "the budget is advisory; a hard limit would reintroduce the proxy"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [True, 123, {}, ["gọn"]])
async def test_rewrite_rejects_non_string_text_before_semantic_validation(text):
    original = "Đây là câu cần diễn đạt ngắn gọn"
    plan, chunks = build_plan([
        DubbingCue(index=1, segment_id="s1", start_ms=0, end_ms=1000, text=original),
    ], DubbingConfig())
    segment = LocalizationSegment(id="s1", source_text="original", translated_text=original,
                                  source_start_ms=0, source_end_ms=1000)

    class AI:
        async def chat(self, **kwargs):
            return json.dumps([{"id": "s1", "text": text}])

    issues = []
    changed = await ls._adapt_chunk_items(
        AI(), plan=plan, chunk=chunks[0], segment_by_id={"s1": segment},
        target_language="Vietnamese", target_ms=1000,
        profile=LocalizationProfile(id="qa", name="QA"), issues=issues,
        state={"measured_duration_ms": 3000},
    )
    assert not changed
    assert plan[0].spoken_text == original
    assert segment.translated_text == original
    assert issues[0]["detail"] == "item_types"


@pytest.mark.asyncio
async def test_malformed_rewrite_records_response_identity_without_raw_content():
    original = "Đây là câu cần diễn đạt ngắn gọn"
    raw = '[{"id":"s1","text":"chưa kết thúc'
    plan, chunks = build_plan([
        DubbingCue(index=1, segment_id="s1", start_ms=0, end_ms=1000, text=original),
    ], DubbingConfig())
    segment = LocalizationSegment(id="s1", source_text="original", translated_text=original,
                                  source_start_ms=0, source_end_ms=1000)

    class AI:
        async def chat(self, **kwargs):
            return raw

    issues = []
    assert not await ls._adapt_chunk_items(
        AI(), plan=plan, chunk=chunks[0], segment_by_id={"s1": segment},
        target_language="Vietnamese", target_ms=1000,
        profile=LocalizationProfile(id="qa", name="QA"), issues=issues,
        state={"measured_duration_ms": 3000},
    )
    assert issues[0]["detail"] == "json_parse"
    assert issues[0]["response_chars"] == len(raw)
    assert len(issues[0]["response_sha256"]) == 64
    assert raw not in json.dumps(issues)


@pytest.mark.asyncio
async def test_rewrite_leaves_completion_headroom_for_reasoning_models():
    # LIVE Ling response exhausted 1000 tokens before returning any JSON
    # (finish_reason=length/content=null). A bounded 4096-token probe returned
    # valid JSON; never parse hidden reasoning as a replacement answer.
    original = "Đây là câu cần diễn đạt ngắn gọn"
    plan, chunks = build_plan([
        DubbingCue(index=1, segment_id="s1", start_ms=0, end_ms=1000, text=original),
    ], DubbingConfig())
    segment = LocalizationSegment(id="s1", source_text="original", translated_text=original,
                                  source_start_ms=0, source_end_ms=1000)

    class AI:
        async def chat(self, **kwargs):
            if kwargs["max_tokens"] <= 1000:
                return ""
            assert kwargs["max_tokens"] <= 4096
            return '[{"id":"s1","text":"Câu cần ngắn gọn"}]'

    issues = []
    changed = await ls._adapt_chunk_items(
        AI(), plan=plan, chunk=chunks[0], segment_by_id={"s1": segment},
        target_language="Vietnamese", target_ms=1000,
        profile=LocalizationProfile(id="qa", name="QA"), issues=issues,
        state={"measured_duration_ms": 3000},
    )
    assert changed
    assert plan[0].spoken_text == "Câu cần ngắn gọn"
    assert not issues
