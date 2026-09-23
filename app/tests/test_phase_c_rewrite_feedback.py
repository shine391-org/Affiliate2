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
    assert issues[0]["reason"] == "rewrite_not_shorter"
    assert "9192" in prompts[0]
    assert "6052" in prompts[0]
    assert "word budget" in prompts[0].lower()
