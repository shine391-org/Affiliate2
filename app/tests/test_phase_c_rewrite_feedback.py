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
async def test_rewrite_rejects_non_shortening_and_uses_measured_feedback(candidate):
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
    assert not changed
    assert plan[0].spoken_text == original
    assert segment.translated_text == original
    assert issues[0]["reason"] == ("rewrite_no_safe_shortening" if candidate == original else "rewrite_not_shorter")
    assert "9192" in prompts[0]
    assert "6052" in prompts[0]
    assert "word budget" in prompts[0].lower()


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
