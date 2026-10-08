"""Focused prompt regressions: measured C7 timing, guard feedback, C6 fidelity."""
import json
from types import SimpleNamespace

import pytest

from backend.core.localization import LocalizationProfile, LocalizationSegment
from backend.services import dubbing_rewrite_guard, localization_service as svc
from backend.services.dubbing_planner import DubbingChunk, PlanItem


def test_rewrite_budget_uses_existing_normal_speed_ceiling_not_emergency_speed():
    # A 1s source window may contain ~1.3s of natural-rate speech because
    # existing postfit safely speeds it up to 1.30x. Leave 5% headroom.
    assert svc._rewrite_speech_budget_ms(1600, 1000, 1.30) == 1235
    assert svc._rewrite_speech_budget_ms(1400, 1000, 1.30) == 1235
    assert svc._rewrite_speech_budget_ms(1800, 1000, 1.00) == 950
    assert svc._rewrite_speech_budget_ms(0, 1000, 1.30) == 0


def test_c6_prompt_prefers_natural_conciseness_without_enforcing_time_quota():
    profile = LocalizationProfile(id="qa", name="QA")
    segment = LocalizationSegment(
        id="s1", source_text="谢谢大家", source_start_ms=0,
        source_end_ms=1000, translation_status="pending",
    )
    prompt = svc._translation_prompt(
        target_language="Vietnamese", batch=[segment],
        transcript_context=segment.source_text, profile=profile,
        for_dub=True,
    )
    assert "source_duration_ms" in prompt
    assert "soft pacing hint" in prompt
    assert "never omit facts to fit time" in prompt
    assert "Return every input ID exactly once" in prompt
    assert "Preserve all numeric facts" in prompt
    assert "JOB NAME DICTIONARY" in prompt


@pytest.mark.asyncio
async def test_c7_prompt_uses_measured_target_and_feedback_is_guard_only(
    monkeypatch,
):
    profile = LocalizationProfile(id="qa", name="QA", dub_speed_max=1.30)
    original = "Tôi không mua món đồ này đâu"
    segment = LocalizationSegment(
        id="s1", source_text="我不买这个",
        translated_text=original, source_start_ms=0,
        source_end_ms=1000, translation_status="success",
    )
    item = PlanItem(
        index=1, segment_id="s1", original_start_ms=0,
        original_end_ms=1000, original_text=segment.source_text,
        spoken_text=original,
    )
    chunk = DubbingChunk(id=1, item_indexes=[0], start_ms=0, end_ms=1000)
    seen_prompts = []
    responses = iter(["Mua món này", "Tôi không mua món này"])

    async def fake_chat(_ai, _fallback_ai, *, messages, **kwargs):
        seen_prompts.append(messages[0]["content"])
        return json.dumps([{"id": "s1", "text": next(responses)}], ensure_ascii=False)

    monkeypatch.setattr(svc, "_call_ai_chat_resilient", fake_chat)
    monkeypatch.setattr(
        dubbing_rewrite_guard, "check_rewrite_meaning",
        lambda _before, after, *_args, **_kwargs: (
            [{"kind": "negation_changed"}] if after == "Mua món này" else []
        ),
    )
    state = {"measured_duration_ms": 1600, "attempt": 1}
    options = dict(
        ai=SimpleNamespace(max_retries=0),
        plan=[item], chunk=chunk, segment_by_id={"s1": segment},
        target_language="Vietnamese", target_ms=1000,
        profile=profile, state=state, issues=[],
    )
    assert not await svc._adapt_chunk_items(**options)
    assert item.spoken_text == original
    assert segment.translated_text == original
    assert "approximately 1235 ms" in seen_prompts[0]
    assert "normal playback speed ceiling 1.30x" in seen_prompts[0]
    assert state["last_meaning_guard"] == [
        {"segment_id": "s1", "kind": "negation_changed"}
    ]

    state["attempt"] = 2
    assert await svc._adapt_chunk_items(**options)
    assert "negation_changed" in seen_prompts[1]
    assert "Mua món này" not in seen_prompts[1]
    assert "last_meaning_guard" not in state
    assert item.spoken_text == "Tôi không mua món này"
    assert segment.translated_text == item.spoken_text
