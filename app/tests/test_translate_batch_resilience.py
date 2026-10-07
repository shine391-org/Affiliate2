# SPDX-License-Identifier: AGPL-3.0-only
"""Phase C5-C8 donor-derived localization regressions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

@pytest.fixture(autouse=True)
def mocked_postfit_measurement(monkeypatch):
    """Unit orchestration uses byte placeholders, NOT playable audio.

    Real measurement/failure gates run unmocked in postfit/rhythm suites.
    """
    from backend.services import localization_service as service

    async def measure(_path, raw_duration_ms, speed_factor):
        return round(raw_duration_ms / speed_factor), -12.0

    monkeypatch.setattr(service, "_measure_fitted_audio", measure)


from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.services import localization_service as ls


def _profile(**updates):
    base = LocalizationProfile(
        id="qa",
        name="QA",
        target_aspect="source",
        glossary={"品牌": "thương hiệu"},
        translation_instructions="Giọng nói tự nhiên.",
        audio_mode="fast_duck",
        alignment_mode="off",
        diarization_mode="off",
        video_retime_mode="off",
    )
    return LocalizationProfile.model_validate({**base.model_dump(), **updates})


def _runtime():
    return ls.TTSRuntimeConfig(
        provider="edge",
        default_voice="vi",
        api_key="",
        model=None,
        base_url=None,
        region=None,
    )


def _seed_reusable_chunk(
    work: Path,
    chunk_id: int,
    text: str,
    speaker_id: str,
    *,
    duration_ms: int | None = None,
):
    runtime = _runtime()
    path = work / f"chunk_{chunk_id:04d}.wav"
    path.write_bytes(b"old")
    identity = ls._chunk_synthesis_identity(
        text=text,
        runtime=runtime,
        voice=runtime.default_voice,
        target_language="Vietnamese",
        speaker_id=speaker_id,
        reference_audio=None,
        reference_text=None,
        reference_language=None,
    )
    ls._write_chunk_identity(path, identity, duration_ms)
    return path


def _transcript():
    return LocalizationTranscript(
        text="第一句 品牌很好",
        language="zh",
        source="whisper",
        segments=[
            LocalizationSegment(
                id="s1", source_text="第一句", source_start_ms=0, source_end_ms=1000,
            ),
            LocalizationSegment(
                id="s2", source_text="品牌很好", source_start_ms=1200, source_end_ms=2200,
            ),
        ],
    )


class _EchoAI:
    def __init__(self, bad_first=False):
        self.prompts = []
        self.bad_first = bad_first
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        raw = prompt.split("SEGMENTS:\n", 1)[1].split("\n\nReturn ONLY", 1)[0]
        items = json.loads(raw)
        if self.bad_first and self.calls == 1:
            return json.dumps([{"id": "wrong", "text": "bad"}])
        return json.dumps(
            [{"id": x["id"], "text": {"第一句": "Câu đầu tiên", "品牌很好": "Thương hiệu rất tốt"}[x["text"]]} for x in items],
            ensure_ascii=False,
        )


def test_profile_resolution_uses_saved_default():
    settings = SimpleNamespace(localization_profiles_json=json.dumps({
        "default_profile_id": "two",
        "profiles": [
            _profile(id="one", name="One").model_dump(),
            _profile(id="two", name="Two", asr_quality="best").model_dump(),
        ],
    }))
    got = ls.resolve_localization_profile(settings)
    assert got.id == "two"
    assert got.asr_quality == "best"


def test_c6_and_c7_can_resolve_different_models(monkeypatch):
    from backend.core.ai_provider import AIProvider

    settings = SimpleNamespace(ai_provider="lmstudio")

    def fake_client(_settings):
        return SimpleNamespace(
            provider=AIProvider.COMPATIBLE,
            model="global-small",
            base_url="http://127.0.0.1:1234/v1",
        )

    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", fake_client)
    profile = _profile(
        translation_model="translator-large",
        rewrite_model="rewrite-small",
    )

    translation_ai = ls._profile_ai_client(settings, profile, "translation")
    rewrite_ai = ls._profile_ai_client(settings, profile, "rewrite")

    assert translation_ai.model == "translator-large"
    assert rewrite_ai.model == "rewrite-small"


def test_c6_translation_prompt_uses_editable_semantics_but_keeps_locked_protocol():
    profile = _profile(
        translation_preset="natural_vietnamese",
        translation_guardrails="C6 SEMANTIC SENTINEL",
    )
    prompt = ls._translation_prompt(
        target_language="Vietnamese",
        batch=_transcript().segments,
        transcript_context="第一句 品牌很好",
        profile=profile,
        for_dub=True,
    )
    assert "C6 SEMANTIC SENTINEL" in prompt
    assert "LOCKED PROTOCOL:" in prompt
    assert "Preserve segment IDs and order." in prompt
    assert "Return every input ID exactly once and no extra IDs." in prompt
    assert "Return only the required JSON array" in prompt
    assert "this protocol wins" in prompt


@pytest.mark.asyncio
async def test_c5_transcribe_passes_exact_profile_model(monkeypatch, tmp_path):
    calls = {}

    class _Whisper:
        async def transcribe(self, path, **kwargs):
            calls.update(kwargs)
            return {
                "text": "hello",
                "language": "en",
                "segments": [{"start": 0, "end": 1, "text": "hello", "words": []}],
            }

    monkeypatch.setattr("backend.services.whisper_service.whisper_service", _Whisper())
    tx = await ls.transcribe_for_localization(
        tmp_path / "x.mp4",
        _profile(asr_quality="best", asr_model="large-v3"),
    )
    assert calls["quality"] == "best"
    assert calls["model_name"] == "large-v3"
    assert tx.segments[0].source_end_ms == 1000


@pytest.mark.asyncio
async def test_c5_hq_profile_calls_alignment_layer(monkeypatch, tmp_path):
    class _Whisper:
        async def transcribe(self, path, **kwargs):
            return {
                "text": "hello",
                "language": "en",
                "segments": [{"start": 0, "end": 1, "text": "hello", "words": []}],
            }

    seen = {}
    from backend.services.alignment_service import AlignmentReport
    fake_report = AlignmentReport(
        alignment_applied=True,
        diarization_applied=False,
        diarization_reason="missing_huggingface_token",
        speaker_count=0,
        device="cpu",
    )

    async def fake_align(path, transcript, profile, **kwargs):
        seen["path"] = Path(path)
        seen["alignment"] = profile.alignment_mode
        return transcript, fake_report

    monkeypatch.setattr("backend.services.whisper_service.whisper_service", _Whisper())
    monkeypatch.setattr("backend.services.alignment_service.align_localization_transcript", fake_align)
    tx = await ls.transcribe_for_localization(
        tmp_path / "x.mp4",
        _profile(alignment_mode="whisperx", diarization_mode="auto"),
    )
    assert seen["alignment"] == "whisperx"
    assert tx.metadata["alignment"]["alignment_applied"] is True


@pytest.mark.asyncio
async def test_c6_prompt_carries_context_glossary_style_and_duration(monkeypatch):
    ai = _EchoAI()
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: ai)
    tx = await ls.translate_transcript(
        _transcript(),
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        for_dub=True,
    )
    prompt = ai.prompts[0]
    assert "第一句 品牌很好" in prompt
    assert "品牌 => thương hiệu" in prompt
    assert "Giọng nói tự nhiên." in prompt
    assert '"source_duration_ms": 1000' in prompt
    assert [s.translated_text for s in tx.segments] == ["Câu đầu tiên", "Thương hiệu rất tốt"]


@pytest.mark.asyncio
async def test_c6_shape_error_splits_instead_of_sliding_ids(monkeypatch):
    ai = _EchoAI(bad_first=True)
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: ai)
    tx = await ls.translate_transcript(
        _transcript(),
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        for_dub=False,
    )
    assert ai.calls == 3
    assert [s.translated_text for s in tx.segments] == ["Câu đầu tiên", "Thương hiệu rất tốt"]


def _patch_dub_io(monkeypatch, tmp_path, durations, generated=None):
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())
    calls = {"n": 0, "fit": 0}

    async def fake_generate(text, *, output_path, voice, **kwargs):
        calls["n"] += 1
        if generated is not None:
            generated.append((text, voice, output_path.name))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        index = min(calls["n"] - 1, len(durations) - 1)
        return durations[index]

    def fake_fit(source, destination, speed_factor):
        calls["fit"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 2.0)
    return calls


@pytest.mark.asyncio
async def test_c7_rewrite_prompt_keeps_locked_core_and_uses_its_own_profile_instructions():
    class CaptureAI:
        def __init__(self):
            self.prompt = ""

        async def chat(self, messages, **kwargs):
            self.prompt = messages[0]["content"]
            return json.dumps([{"id": "s1", "text": "câu rất dài"}], ensure_ascii=False)

    ai = CaptureAI()
    plan = [
        ls.PlanItem(
            index=0,
            segment_id="s1",
            original_start_ms=0,
            original_end_ms=1000,
            original_text="một câu rất dài",
            spoken_text="một câu rất dài",
            estimated_duration_ms=1800,
        )
    ]
    chunk = ls.DubbingChunk(
        id=1,
        item_indexes=[0],
        start_ms=0,
        end_ms=1000,
    )
    segment = LocalizationSegment(
        id="s1",
        source_text="原文",
        translated_text="một câu rất dài",
        source_start_ms=0,
        source_end_ms=1000,
        translation_status="success",
    )
    profile = _profile(
        translation_instructions="C6 ONLY SENTINEL",
        rewrite_instructions="C7 ONLY SENTINEL",
        rewrite_guardrails="C7 SEMANTIC SENTINEL",
    )

    changed = await ls._adapt_chunk_items(
        ai,
        plan=plan,
        chunk=chunk,
        segment_by_id={"s1": segment},
        target_language="Vietnamese",
        target_ms=1000,
        profile=profile,
        state={"measured_duration_ms": 1800, "attempt": 1},
    )

    assert changed is True
    assert "C7 ONLY SENTINEL" in ai.prompt
    assert "C6 ONLY SENTINEL" not in ai.prompt
    assert "C7 SEMANTIC SENTINEL" in ai.prompt
    assert "LOCKED PROTOCOL:" in ai.prompt
    assert "Preserve every item ID and order." in ai.prompt
    assert "Return every input ID exactly once and no extra IDs." in ai.prompt
    assert "this protocol wins" in ai.prompt
    assert "semantic guardrails and locked protocol above remain active" in ai.prompt
    assert segment.translated_text == "câu rất dài"


@pytest.mark.asyncio
async def test_c7_short_sentences_stay_separate_but_continuations_merge(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        text="Yes and then continue",
        segments=[
            LocalizationSegment(
                id="s1", source_text="Yes.", translated_text="Có.",
                source_start_ms=0, source_end_ms=480, translation_status="success",
            ),
            LocalizationSegment(
                id="s2", source_text="and then", translated_text="và sau đó",
                source_start_ms=500, source_end_ms=1000, translation_status="success",
            ),
            LocalizationSegment(
                id="s3", source_text="continue", translated_text="tiếp tục",
                source_start_ms=1020, source_end_ms=1750, translation_status="success",
            ),
        ],
    )
    generated = []
    calls = _patch_dub_io(monkeypatch, tmp_path, [400, 1250], generated)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_speed_max=1.30),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert result.chunk_count == 2
    assert calls["n"] == 2
    assert generated[0][0] == "Có."
    assert generated[1][0] == "và sau đó tiếp tục"
    assert result.max_speed_factor <= 1.15
    assert all(seg.target_start_ms is not None for seg in result.transcript.segments)


@pytest.mark.asyncio
async def test_c7_speaker_change_forces_chunk_boundary(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", speaker_id="SPEAKER_00", source_text="one", translated_text="một",
            source_start_ms=0, source_end_ms=500, translation_status="success",
        ),
        LocalizationSegment(
            id="s2", speaker_id="SPEAKER_01", source_text="two", translated_text="hai",
            source_start_ms=550, source_end_ms=1100, translation_status="success",
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [400, 400])
    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert result.chunk_count == 2
    assert [c.speaker_id for c in result.chunks] == ["SPEAKER_00", "SPEAKER_01"]


@pytest.mark.asyncio
async def test_c7_last_chunk_borrows_trailing_silence_without_timing_override(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="one",
            translated_text="một",
            source_start_ms=0,
            source_end_ms=500,
            translation_status="success",
        )
    ])
    _patch_dub_io(monkeypatch, tmp_path, [800])
    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    # Spend only the measured demand at natural speed, not all 1.5s silence.
    assert result.chunks is not None
    assert result.chunks[0].end_ms == 800
    assert result.chunks[0].available_ms == 800
    assert result.chunks[0].speed_factor == 1.0
    assert result.chunks[0].start_ms == 0


@pytest.mark.asyncio
async def test_c7_expert_voice_and_timing_override_reaches_planner(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", speaker_id="SPEAKER_00", source_text="one", translated_text="một",
            source_start_ms=0, source_end_ms=500, translation_status="success",
            overrides={"target_start_ms": 100, "target_end_ms": 900, "voice_id": "voice-special"},
        )
    ])
    generated = []
    _patch_dub_io(monkeypatch, tmp_path, [700], generated)
    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert generated[0][1] == "voice-special"
    assert result.chunks[0].start_ms == 100
    assert result.chunks[0].end_ms == 900


@pytest.mark.asyncio
async def test_c7_overlong_chunk_rewrites_then_regenerates(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="one", translated_text="một câu rất dài",
            source_start_ms=0, source_end_ms=900, translation_status="success",
        ),
        LocalizationSegment(
            id="s2", source_text="two", translated_text="thêm một câu dài",
            source_start_ms=950, source_end_ms=2000, translation_status="success",
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [4000, 2200])

    async def fake_adapt(ai, *, plan, chunk, segment_by_id, **kwargs):
        for idx in chunk.item_indexes:
            plan[idx].spoken_text = "ngắn"
            plan[idx].estimated_duration_ms = 300
            segment_by_id[plan[idx].segment_id].translated_text = "ngắn"
        return True

    monkeypatch.setattr(ls, "_adapt_chunk_items", fake_adapt)
    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_speed_max=1.30, dub_rewrite_attempts=2),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert result.rewrite_count >= 1
    assert all(seg.translated_text == "ngắn" for seg in result.transcript.segments)


@pytest.mark.asyncio
async def test_c7_slowdown_accounts_for_integer_timestamp_rounding(monkeypatch, tmp_path):
    # Exact real-job regression: the real-valued factor rounds this window to
    # 6535ms, requiring 1.3000765x instead of the allowed 1.3x.
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="one", translated_text="một",
            source_start_ms=12765, source_end_ms=18817,
            translation_status="success",
            # No silence budget: exercise rounding/ceiling, not allocation.
            overrides={"target_start_ms": 12765, "target_end_ms": 18817},
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [8496])
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration", lambda *a, **k: 18.817,
    )
    result = await ls.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=_profile(dub_rewrite_attempts=0, video_retime_mode="fallback"),
        user_settings=object(), work_dir=tmp_path / "work",
    )
    assert 1.0 < result.video_slowdown <= 1.12
    assert result.chunks is not None
    assert result.chunks[0].speed_factor <= 1.3
    assert result.chunks[0].available_ms >= 8496 / 1.3
    assert not any(i["reason"] == "timing_overflow" for i in result.issues)


@pytest.mark.asyncio
async def test_c7_rounding_guard_does_not_hide_overflow_at_retime_ceiling(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="one", translated_text="một",
            source_start_ms=12765, source_end_ms=18817,
            translation_status="success",
            # No silence budget: exercise rounding/ceiling, not allocation.
            overrides={"target_start_ms": 12765, "target_end_ms": 18817},
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [10000])
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration", lambda *a, **k: 18.817,
    )
    with pytest.raises(ls.LocalizationTimingError) as error:
        await ls.synthesize_timed_dub(
            tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
            profile=_profile(dub_rewrite_attempts=0, video_retime_mode="fallback"),
            user_settings=object(), work_dir=tmp_path / "work",
        )
    assert error.value.issues[0]["available_ms"] == round(18817 * 1.12) - round(12765 * 1.12)
    assert error.value.issues[0]["reason"] == "timing_overflow"


@pytest.mark.asyncio
async def test_c7_rejected_rewrite_retries_with_measured_feedback(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="one", translated_text="một câu dài",
            source_start_ms=0, source_end_ms=2000, translation_status="success",
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [3000, 1800])
    seen = []

    async def adapt(ai, *, plan, chunk, segment_by_id, state, **kwargs):
        seen.append(dict(state))
        if len(seen) == 1:
            return False
        plan[0].spoken_text = "ngắn"
        segment_by_id["s1"].translated_text = "ngắn"
        return True

    monkeypatch.setattr(ls, "_adapt_chunk_items", adapt)
    result = await ls.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=_profile(dub_rewrite_attempts=2), user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert seen == [
        {"measured_duration_ms": 3000, "attempt": 1},
        {"measured_duration_ms": 3000, "attempt": 2},
    ]
    assert result.rewrite_count == 1


@pytest.mark.asyncio
async def test_c7_over_max_after_rewrite_fails_without_retime(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=500, translation_status="success",
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [2000])

    async def no_change(*args, **kwargs):
        return False

    monkeypatch.setattr(ls, "_adapt_chunk_items", no_change)
    # This case is specifically about an overlong chunk with no retime room.
    # The shared helper exposes a 2s source timeline, which now legitimately
    # lets a normal final chunk borrow trailing silence. Pin the canonical
    # source end to the cue end so the test keeps exercising overflow rather
    # than the newer trailing-silence behavior.
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 0.5,
    )
    with pytest.raises(ls.LocalizationTimingError) as exc:
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(dub_speed_max=1.30, video_retime_mode="off"),
            user_settings=object(),
            work_dir=tmp_path / "work",
        )
    assert exc.value.issues[0]["reason"] == "timing_overflow"


@pytest.mark.asyncio
async def test_c7_timing_failure_preserves_actual_fallback_route_audit(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=500, translation_status="success",
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [2000])
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 0.5,
    )

    primary = SimpleNamespace(connection_name="hermes-main")
    fallback = SimpleNamespace(connection_name="lmstudio-qwen")
    monkeypatch.setattr(
        ls,
        "_profile_ai_route_clients_compat",
        lambda *_args, **_kwargs: [primary, fallback],
    )

    async def failover_without_fit(*args, route_state=None, **kwargs):
        if route_state is not None:
            route_state.failure_chain = [{
                "connection_id": "hermes-main",
                "provider": "compatible",
                "model": "longcat",
                "attempt": 1,
                "error_type": "TimeoutError",
                "error": "timed out",
            }]
            route_state.sticky_index = 1
            route_state.attempt_count = 2
            route_state.success_count = 1
        return False

    monkeypatch.setattr(ls, "_adapt_chunk_items", failover_without_fit)

    with pytest.raises(ls.LocalizationTimingError) as exc:
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(
                dub_rewrite_attempts=1,
                dub_speed_max=1.30,
                video_retime_mode="off",
            ),
            user_settings=object(),
            work_dir=tmp_path / "work",
        )

    audit = exc.value.rewrite_ai
    assert audit["selected_connection_id"] == "lmstudio-qwen"
    assert audit["sticky_route_index"] == 1
    assert audit["attempted"] is True
    assert audit["attempt_count"] == 2
    assert audit["success_count"] == 1
    assert audit["failure_chain"][0]["connection_id"] == "hermes-main"


@pytest.mark.asyncio
async def test_c7_targeted_regeneration_reuses_other_raw_chunks(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", speaker_id="A", source_text="one", translated_text="một",
            source_start_ms=0, source_end_ms=500, translation_status="success",
        ),
        LocalizationSegment(
            id="s2", speaker_id="B", source_text="two", translated_text="hai",
            source_start_ms=1000, source_end_ms=1500, translation_status="success",
        ),
    ])
    work = tmp_path / "work"
    work.mkdir()
    _seed_reusable_chunk(work, 1, "một", "A")
    _seed_reusable_chunk(work, 2, "hai", "B")

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())
    async def fake_duration(path):
        return 400

    generated = []
    async def fake_generate(text, *, output_path, voice, **kwargs):
        generated.append(output_path.name)
        output_path.write_bytes(b"new")
        return 400

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_audio_duration_ms", fake_duration)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 2.0)

    await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=work,
        regenerate_chunk_ids={2},
        reuse_existing_chunks=True,
    )
    assert [name.split(".pending", 1)[0] for name in generated] == ["chunk_0002"]
    assert all(name.endswith(".wav") for name in generated)
    assert (work / "chunk_0002.wav").read_bytes() == b"new"



@pytest.mark.asyncio
async def test_c7_rewrite_retries_transient_provider_failure_before_disabling(
    monkeypatch,
):
    plan = [
        ls.PlanItem(
            index=0,
            segment_id="s1",
            original_start_ms=0,
            original_end_ms=1000,
            original_text="một câu rất dài",
            spoken_text="một câu rất dài",
            estimated_duration_ms=1800,
        )
    ]
    chunk = ls.DubbingChunk(
        id=1,
        item_indexes=[0],
        start_ms=0,
        end_ms=1000,
    )
    segment = LocalizationSegment(
        id="s1",
        source_text="原文",
        translated_text="một câu rất dài",
        source_start_ms=0,
        source_end_ms=1000,
        translation_status="success",
    )

    class Transient503(RuntimeError):
        status_code = 503

    class RecoveringAI:
        def __init__(self):
            self.calls = 0
            self.max_retries = None

        async def chat(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise Transient503("provider overloaded")
            return json.dumps([{"id": "s1", "text": "câu rất dài"}], ensure_ascii=False)

    async def no_sleep(_delay):
        return None

    ai = RecoveringAI()
    monkeypatch.setattr(ls.asyncio, "sleep", no_sleep)
    issues = []
    state = {"measured_duration_ms": 1800, "attempt": 1}

    changed = await ls._adapt_chunk_items(
        ai,
        plan=plan,
        chunk=chunk,
        segment_by_id={"s1": segment},
        target_language="Vietnamese",
        target_ms=1000,
        profile=_profile(),
        issues=issues,
        state=state,
    )

    assert changed is True
    assert ai.calls == 2
    assert ai.max_retries == 0
    assert state.get("unavailable") is not True
    assert issues == []
    assert segment.translated_text == "câu rất dài"


@pytest.mark.asyncio
async def test_c7_rewrite_exhausts_bounded_retries_before_opening_the_circuit(
    monkeypatch,
):
    """The retry budget must stay bounded.

    This is the pair of the recovery test above: when every transient attempt
    fails, the chunk must be marked unavailable so the caller can disable the
    optional rewrite for the rest of the job. A retry loop that never gives up
    would hang the stage instead of degrading.
    """
    plan = [
        ls.PlanItem(
            index=0,
            segment_id="s1",
            original_start_ms=0,
            original_end_ms=1000,
            original_text="một câu rất dài",
            spoken_text="một câu rất dài",
            estimated_duration_ms=1800,
        )
    ]
    chunk = ls.DubbingChunk(id=1, item_indexes=[0], start_ms=0, end_ms=1000)
    segment = LocalizationSegment(
        id="s1",
        source_text="原文",
        translated_text="một câu rất dài",
        source_start_ms=0,
        source_end_ms=1000,
        translation_status="success",
    )

    class Transient503(RuntimeError):
        status_code = 503

    class DeadAI:
        def __init__(self):
            self.calls = 0
            self.max_retries = None

        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise Transient503("provider overloaded")

    async def no_sleep(_delay):
        return None

    ai = DeadAI()
    monkeypatch.setattr(ls.asyncio, "sleep", no_sleep)
    issues = []
    state = {"measured_duration_ms": 1800, "attempt": 1}

    changed = await ls._adapt_chunk_items(
        ai,
        plan=plan,
        chunk=chunk,
        segment_by_id={"s1": segment},
        target_language="Vietnamese",
        target_ms=1000,
        profile=_profile(),
        issues=issues,
        state=state,
    )

    assert changed is False
    # Bounded: exactly the helper's budget, then it stops.
    assert ai.calls == 3
    assert state.get("unavailable") is False
    assert state["retry_scope"] == "chunk"
    # The plan and caption transcript must stay untouched so a failed rewrite
    # can never half-apply.
    assert plan[0].spoken_text == "một câu rất dài"
    assert segment.translated_text == "một câu rất dài"
    assert [i["reason"] for i in issues] == ["rewrite_ai_transient"]


@pytest.mark.asyncio
async def test_c7_optional_transient_failure_does_not_disable_later_chunks(
    monkeypatch, tmp_path,
):
    long_text = "đây là một câu tiếng Việt rất dài cần được rút gọn trước khi đọc"
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id=f"s{i}",
            speaker_id=f"S{i}",
            source_text=f"source {i}",
            translated_text=long_text,
            source_start_ms=i * 1000,
            source_end_ms=i * 1000 + 500,
            translation_status="success",
            overrides={"target_start_ms": i * 1000, "target_end_ms": i * 1000 + 500},
        )
        for i in range(3)
    ])

    class TimeoutAI:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("local rewrite provider timeout")

    async def no_sleep(_delay):
        return None

    ai = TimeoutAI()
    monkeypatch.setattr(ls.asyncio, "sleep", no_sleep)
    # Locked 500ms windows make this outage probe independent of silence
    # allocation. 660ms requires 1.32x: normal overflow, emergency-review fits.
    calls = _patch_dub_io(monkeypatch, tmp_path, [660, 660, 660])
    # Three source cues span 3 seconds; a 2s source stub is invalid at EOF.
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 3.0)
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: ai)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_rewrite_attempts=2, dub_speed_max=1.30),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )

    assert ai.calls == 9  # Exactly three bounded transport attempts per chunk.
    assert calls["n"] == 3
    transient = [
        issue for issue in result.issues
        if issue["reason"] == "rewrite_ai_transient"
    ]
    assert len(transient) == 3
    assert [i["chunk_id"] for i in transient] == [1, 2, 3]
    assert all(i["retry_scope"] == "chunk" for i in transient)
    assert result.chunk_count == 3


@pytest.mark.asyncio
async def test_c7_targeted_regeneration_keeps_overlong_non_target_immutable(
    monkeypatch, tmp_path,
):
    non_target_text = "giữ nguyên chunk một"
    target_text = "đây là câu rất dài của chunk hai cần rút gọn trước khi tổng hợp"
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text=non_target_text,
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        ),
        LocalizationSegment(
            id="s2",
            speaker_id="B",
            source_text="two",
            translated_text=target_text,
            source_start_ms=1000,
            source_end_ms=2000,
            translation_status="success",
        ),
    ])
    work = tmp_path / "work"
    work.mkdir()
    _seed_reusable_chunk(work, 1, non_target_text, "A", duration_ms=1310)
    _seed_reusable_chunk(work, 2, target_text, "B", duration_ms=900)

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    adapted = []
    async def fake_adapt(ai, *, plan, chunk, segment_by_id, **kwargs):
        adapted.append(chunk.id)
        for idx in chunk.item_indexes:
            plan[idx].spoken_text = "ngắn"
            plan[idx].estimated_duration_ms = 250
            segment_by_id[plan[idx].segment_id].translated_text = "ngắn"
        return True

    generated = []
    synth_calls = []

    async def fake_generate(text, *, output_path, **kwargs):
        generated.append(output_path.name)
        synth_calls.append(text)
        output_path.write_bytes(b"new")
        # The regeneration target must be genuinely MEASURED-overlong before the
        # rewrite is allowed; since Y1 the estimate cannot authorise it.
        return 1500 if len(synth_calls) == 1 else 900

    fit_calls = []
    def fake_fit(source, destination, speed_factor):
        fit_calls.append((source.name, speed_factor))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_adapt_chunk_items", fake_adapt)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 2.0,
    )

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(
            dub_rewrite_attempts=2,
            dub_speed_max=1.30,
            video_retime_mode="fallback",
        ),
        user_settings=object(),
        work_dir=work,
        regenerate_chunk_ids={2},
        reuse_existing_chunks=True,
    )

    assert adapted == [2]
    # Target chunk synthesized before and after its measured-overflow rewrite;
    # the non-target chunk is reused and never synthesized.
    assert [name.split(".pending", 1)[0] for name in generated] == [
        "chunk_0002", "chunk_0002",
    ]
    assert len(set(generated)) == len(generated), "Every generation owns a distinct pending path"
    assert all(name.endswith(".wav") for name in generated)
    assert result.transcript.segments[0].translated_text == non_target_text
    assert (work / "chunk_0001.wav").read_bytes() == b"old"
    assert result.transcript.segments[1].translated_text == "ngắn"
    assert fit_calls and fit_calls[0][0] == "chunk_0001.wav"


@pytest.mark.asyncio
async def test_c7_targeted_non_target_probe_failure_never_resynthesizes(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text="giữ nguyên",
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        ),
        LocalizationSegment(
            id="s2",
            speaker_id="B",
            source_text="two",
            translated_text="target",
            source_start_ms=1000,
            source_end_ms=2000,
            translation_status="success",
        ),
    ])
    work = tmp_path / "work"
    work.mkdir()
    _seed_reusable_chunk(work, 1, "giữ nguyên", "A")
    _seed_reusable_chunk(work, 2, "target", "B")

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    async def fail_duration(path):
        raise RuntimeError("ffprobe failed")

    async def fail_generate(*args, **kwargs):
        pytest.fail("non-target chunk must never be resynthesized")

    monkeypatch.setattr(ls, "_audio_duration_ms", fail_duration)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fail_generate)

    with pytest.raises(RuntimeError, match="could not be reused/measured"):
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(),
            user_settings=object(),
            work_dir=work,
            regenerate_chunk_ids={2},
            reuse_existing_chunks=True,
        )

    assert (work / "chunk_0001.wav").read_bytes() == b"old"


@pytest.mark.asyncio
async def test_c7_natural_rate_chunks_skip_audio_fit_ffmpeg(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id=f"s{i}",
            speaker_id=f"S{i}",
            source_text=f"source {i}",
            translated_text=f"ngắn {i}",
            source_start_ms=i * 1000,
            source_end_ms=(i + 1) * 1000,
            translation_status="success",
        )
        for i in range(3)
    ])
    calls = _patch_dub_io(monkeypatch, tmp_path, [800, 900, 700])
    # Three source cues span 3 seconds; a 2s source stub is invalid at EOF.
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 3.0)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )

    assert result.chunk_count == 3
    assert calls["fit"] == 0


@pytest.mark.asyncio
async def test_c7_only_material_speed_change_runs_audio_fit_ffmpeg(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id=f"s{i}",
            speaker_id=f"S{i}",
            source_text=f"source {i}",
            translated_text=f"ngắn {i}",
            source_start_ms=i * 1000,
            source_end_ms=(i + 1) * 1000,
            translation_status="success",
        )
        for i in range(3)
    ])
    calls = _patch_dub_io(monkeypatch, tmp_path, [1100, 800, 800])
    # Three source cues span 3 seconds; a 2s source stub is invalid at EOF.
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 3.0)

    await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_speed_max=1.30),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )

    assert calls["fit"] == 1


@pytest.mark.asyncio
async def test_c7_valid_cached_duration_skips_raw_ffprobe(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text="ngắn",
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        )
    ])
    work = tmp_path / "work"
    work.mkdir()
    _seed_reusable_chunk(work, 1, "ngắn", "A", duration_ms=800)

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    async def fail_duration(*args, **kwargs):
        pytest.fail("_audio_duration_ms must not probe a valid cached duration")

    async def fail_generate(*args, **kwargs):
        pytest.fail("valid reusable raw audio must not be regenerated")

    def fail_fit(*args, **kwargs):
        pytest.fail("1.0x reusable raw audio must not be fitted")

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_audio_duration_ms", fail_duration)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fail_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fail_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 1.0,
    )

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=work,
        reuse_existing_chunks=True,
    )

    assert result.chunk_count == 1
    assert result.max_fitted_speed_factor == 1.0


@pytest.mark.asyncio
async def test_c7_missing_cached_duration_probes_once_and_heals_sidecar(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text="ngắn",
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        )
    ])
    work = tmp_path / "work"
    work.mkdir()
    raw = _seed_reusable_chunk(work, 1, "ngắn", "A")

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())
    probes = {"n": 0}

    async def fake_duration(path):
        probes["n"] += 1
        return 800

    async def fail_generate(*args, **kwargs):
        pytest.fail("valid identity must reuse raw audio after duration probe")

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_audio_duration_ms", fake_duration)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fail_generate)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 1.0,
    )

    await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=work,
        reuse_existing_chunks=True,
    )

    assert probes["n"] == 1
    sidecar = json.loads(ls._chunk_identity_path(raw).read_text(encoding="utf-8"))
    assert sidecar["duration_ms"] == 800


def test_c7_captions_use_fitted_target_timing_and_speaker():
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id="s1",
        speaker_id="SPEAKER_00",
        source_text="source",
        translated_text="tiếng Việt",
        source_start_ms=1000,
        source_end_ms=2000,
        target_start_ms=1200,
        target_end_ms=2300,
        translation_status="success",
        timing_status="adjusted",
    )])
    assert ls.caption_segments_from_localization(tx) == [{
        "start": 1.2,
        "end": 2.3,
        "text": "tiếng Việt",
        "speaker_id": "SPEAKER_00",
    }]


def test_c8_hq_clean_mix_builds_audio_only_from_clean_background(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    dub = tmp_path / "dub.wav"
    bgm = tmp_path / "bgm.wav"
    out = tmp_path / "mixed.wav"
    for path in (source, dub, bgm):
        path.write_bytes(b"x")

    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"mixed")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ls.subprocess, "run", fake_run)
    got = ls._mix_audio_sync(
        source,
        dub,
        out,
        audio_mode="hq_clean",
        background_file=bgm,
        preserve_background=True,
        background_gain_db=-6.0,
        dub_gain_db=0.0,
        video_slowdown=1.0,
    )
    command = " ".join(captured["cmd"])
    assert got == out
    assert str(bgm) in command
    assert "sidechaincompress" not in command
    assert "alimiter=limit=0.95" in command
    assert "-c:v" not in command



@pytest.mark.asyncio
async def test_gpt_sovits_auto_reference_selects_longest_clean_segment_per_speaker(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(
        language="en",
        segments=[
            LocalizationSegment(
                id="a1", speaker_id="A", source_text="short",
                source_start_ms=0, source_end_ms=1800,
            ),
            LocalizationSegment(
                id="a2", speaker_id="A", source_text="best reference",
                source_start_ms=2000, source_end_ms=6000,
            ),
            LocalizationSegment(
                id="b1", speaker_id="B", source_text="speaker b",
                source_start_ms=6500, source_end_ms=9000,
            ),
        ],
    )
    media = tmp_path / "vocals.wav"
    media.write_bytes(b"audio")
    seen = []

    def fake_extract(media_path, output_path, *, start_ms, end_ms):
        seen.append((output_path.name, start_ms, end_ms))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ref")
        return output_path

    monkeypatch.setattr(ls, "_extract_reference_clip_sync", fake_extract)
    refs = await ls._build_auto_voice_references(
        tx,
        reference_media=media,
        profile=_profile(
            tts_provider="gpt_sovits",
            voice_clone_reference_mode="per_speaker_auto",
        ),
        work_dir=tmp_path / "work",
    )
    assert set(refs) == {"A", "B"}
    assert refs["A"]["text"] == "best reference"
    assert refs["A"]["source"] == "auto"
    assert ("A.wav", 2000, 6000) in seen
    assert ("B.wav", 6500, 9000) in seen


@pytest.mark.asyncio
async def test_manual_clone_reference_overrides_auto_extraction(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        language="en",
        segments=[
            LocalizationSegment(
                id="a1", speaker_id="A", source_text="reference",
                source_start_ms=0, source_end_ms=3000,
            )
        ],
    )

    def should_not_extract(*args, **kwargs):
        raise AssertionError("manual reference must win")

    monkeypatch.setattr(ls, "_extract_reference_clip_sync", should_not_extract)
    refs = await ls._build_auto_voice_references(
        tx,
        reference_media=tmp_path / "vocals.wav",
        profile=_profile(
            tts_provider="gpt_sovits",
            speaker_reference_audio={"A": "manual.wav"},
            voice_clone_reference_mode="per_speaker_auto",
        ),
        work_dir=tmp_path / "work",
    )
    assert refs["A"]["audio"] == "manual.wav"
    assert refs["A"]["source"] == "manual"



def test_tts_config_rejects_profile_provider_mismatch():
    settings = SimpleNamespace(
        tts_provider="azure_tts",
        preferred_tts_voice=None,
        tts_api_key_encrypted=None,
        tts_model=None,
        tts_base_url=None,
        tts_region="southeastasia",
        preferred_voice_id="21m00Tcm4TlvDq8ikWAM",
        ai_provider=None,
        ai_api_key_encrypted=None,
    )
    with pytest.raises(ValueError, match="requests TTS provider"):
        ls._tts_config(
            settings,
            _profile(tts_provider="elevenlabs"),
            "Vietnamese",
        )


def test_phase_c_tts_does_not_inherit_legacy_generator_voice_id():
    from backend.services.tts_service import TTSProvider

    settings = SimpleNamespace(
        tts_provider="edge_tts",
        preferred_tts_voice=None,
        preferred_voice_id="21m00Tcm4TlvDq8ikWAM",
        tts_api_key_encrypted=None,
        tts_model=None,
        tts_base_url=None,
        tts_region=None,
        ai_provider=None,
        ai_api_key_encrypted=None,
    )
    runtime = ls._tts_config(settings, _profile(tts_provider=None), "Vietnamese")
    assert runtime.provider == TTSProvider.EDGE_TTS
    assert runtime.default_voice == "vi-VN-HoaiMyNeural"


def test_phase_c_edge_default_voice_follows_target_language():
    settings = SimpleNamespace(
        tts_provider="edge_tts",
        preferred_tts_voice=None,
        preferred_voice_id="legacy-id-must-not-win",
        tts_api_key_encrypted=None,
        tts_model=None,
        tts_base_url=None,
        tts_region=None,
        ai_provider=None,
        ai_api_key_encrypted=None,
    )
    runtime = ls._tts_config(settings, _profile(tts_provider=None), "English")
    assert runtime.default_voice == "en-US-AndrewMultilingualNeural"



def test_materialized_profile_snapshots_non_secret_runtime_bindings(monkeypatch):
    fake_ai = SimpleNamespace(
        provider=SimpleNamespace(value="compatible"),
        model="qwen-local",
        base_url="http://localhost:1234/v1",
    )
    fake_tts = ls.TTSRuntimeConfig(
        provider=SimpleNamespace(value="edge_tts"),
        default_voice="vi-VN-HoaiMyNeural",
        api_key="secret-must-not-be-persisted",
        model=None,
        base_url=None,
        region=None,
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: fake_ai)
    monkeypatch.setattr(ls, "_tts_config", lambda *_: fake_tts)

    got = ls.materialize_localization_profile(
        object(),
        _profile(),
        target_language="Vietnamese",
        for_dub=True,
    )
    assert got.translation_provider == "compatible"
    assert got.translation_model == "qwen-local"
    # Connection endpoints stay live by connection_id unless the profile
    # explicitly pins a per-route base_url override.
    assert got.translation_base_url is None
    assert got.translation_routes[0].base_url is None
    assert got.tts_provider == "edge_tts"
    assert got.tts_voice == "vi-VN-HoaiMyNeural"
    assert "secret-must-not-be-persisted" not in got.model_dump_json()


def test_captions_only_profile_materialization_does_not_require_tts(monkeypatch):
    fake_ai = SimpleNamespace(
        provider=SimpleNamespace(value="compatible"),
        model="qwen-local",
        base_url="http://localhost:1234/v1",
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: fake_ai)

    def fail_tts(*args, **kwargs):
        raise AssertionError("captions-only materialization must not resolve TTS")

    monkeypatch.setattr(ls, "_tts_config", fail_tts)
    got = ls.materialize_localization_profile(
        object(),
        _profile(),
        target_language="Vietnamese",
        for_dub=False,
    )
    assert got.translation_model == "qwen-local"



def test_dubbing_rewrite_persistence_keeps_translation_text_but_not_target_timing():
    base = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="source",
            translated_text="bản dịch dài",
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        )
    ])
    dubbed = base.model_copy(deep=True)
    dubbed.segments[0].translated_text = "bản ngắn"
    dubbed.segments[0].target_start_ms = 100
    dubbed.segments[0].target_end_ms = 900
    dubbed.segments[0].tts_duration_ms = 800
    dubbed.segments[0].timing_status = "adjusted"

    got = ls.persist_dubbing_rewrites(base, dubbed)
    assert got.segments[0].translated_text == "bản ngắn"
    assert got.segments[0].translation_status == "success"
    assert got.segments[0].target_start_ms is None
    assert got.segments[0].target_end_ms is None
    assert got.segments[0].tts_duration_ms is None
    assert got.segments[0].timing_status == "pending"



@pytest.mark.asyncio
async def test_c7_ai_rewrite_timeout_degrades_to_tts_instead_of_failing(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="A source line that is intentionally verbose.",
            translated_text=(
                "Đây là một câu tiếng Việt cố ý rất dài để bộ ước lượng "
                "thử gọi bước rút gọn trước khi tổng hợp giọng nói."
            ),
            source_start_ms=0,
            source_end_ms=500,
            translation_status="success",
        )
    ])

    class SlowAI:
        calls = 0
        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("LM Studio rewrite timeout")

    slow = SlowAI()
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: slow)
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    async def fake_generate(text, *, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        # Measured TTS overflows its allocated window, so the measured loop --
        # the only rewrite authority since Y1 removed the estimate-driven
        # branch -- attempts a rewrite. Its timeout must degrade to plain TTS
        # instead of failing the job. allocate_silence grows the 500 ms source
        # window to ~1000 ms, so the measured value must exceed THAT, not the
        # raw source window.
        return 1400

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 1.0,
    )

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(
            dub_rewrite_attempts=2,
            dub_speed_max=1.30,
            video_retime_mode="fallback",
        ),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )

    # A transient failure exhausts this chunk's bounded transport attempts,
    # without falsely declaring every later chunk's provider unavailable.
    assert slow.calls == 3
    assert result.chunk_count == 1
    assert any(
        issue["reason"] == "rewrite_ai_transient"
        for issue in result.issues
    )
    assert result.transcript.segments[0].target_start_ms is not None


@pytest.mark.asyncio
async def test_adapt_chunk_timeout_is_best_effort_and_records_issue():
    from backend.services.dubbing_planner import DubbingCue, DubbingConfig, build_plan

    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="source",
            translated_text="bản dịch",
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        )
    ])
    plan, chunks = build_plan(
        [DubbingCue(
            index=1,
            segment_id="s1",
            start_ms=0,
            end_ms=1000,
            text="bản dịch",
        )],
        DubbingConfig(),
        language="Vietnamese",
    )

    class TimeoutAI:
        async def chat(self, *args, **kwargs):
            raise TimeoutError("slow local model")

    issues = []
    state = {"measured_duration_ms": 1800, "attempt": 1}
    changed = await ls._adapt_chunk_items(
        TimeoutAI(),
        plan=plan,
        chunk=chunks[0],
        segment_by_id={"s1": tx.segments[0]},
        target_language="Vietnamese",
        target_ms=1000,
        profile=_profile(),
        issues=issues,
        state=state,
    )
    assert changed is False
    assert state["unavailable"] is False
    assert state["retry_scope"] == "chunk"
    assert issues[0]["reason"] == "rewrite_ai_transient"
    assert issues[0]["error_type"] == "TimeoutError"

@pytest.mark.asyncio
async def test_c7_rewrite_client_setup_failure_is_non_fatal(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="source",
            translated_text=(
                "Đây là một câu tiếng Việt cố ý rất dài để kích hoạt bước "
                "rút gọn sau khi đo giọng nói."
            ),
            source_start_ms=0,
            source_end_ms=500,
            translation_status="success",
        )
    ])

    def fail_ai(*args, **kwargs):
        raise ValueError("saved rewrite provider is temporarily unavailable")

    monkeypatch.setattr(ls, "_profile_ai_client", fail_ai)
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    async def fake_generate(text, *, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        return 1400

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 1.0,
    )

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(
            dub_rewrite_attempts=2,
            dub_speed_max=1.30,
            video_retime_mode="fallback",
        ),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )

    assert result.chunk_count == 1
    issue = next(
        item for item in result.issues
        if item["reason"] == "rewrite_ai_unavailable"
    )
    assert issue["error_type"] == "ValueError"
    assert result.transcript.segments[0].target_start_ms is not None


@pytest.mark.asyncio
async def test_c7_targeted_regeneration_never_rewrites_reused_chunk(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text=(
                "đây là câu rất dài dùng để buộc estimator muốn rewrite "
                "nhưng chunk này phải được reuse nguyên trạng"
            ),
            source_start_ms=0,
            source_end_ms=500,
            translation_status="success",
        ),
        LocalizationSegment(
            id="s2",
            speaker_id="B",
            source_text="two",
            translated_text=(
                "đây cũng là câu dài để chunk mục tiêu được phép rewrite "
                "trước khi regenerate"
            ),
            source_start_ms=1000,
            source_end_ms=1500,
            translation_status="success",
        ),
    ])
    work = tmp_path / "work"
    work.mkdir()
    # Seeded durations make both chunks genuinely MEASURED-overlong (500 ms
    # windows). Since Y1 the estimate no longer authorises a rewrite, so the
    # measured duration is what must drive chunk 2's rewrite; chunk 1 must stay
    # immutable because it is not a regeneration target.
    _seed_reusable_chunk(
        work,
        1,
        "đây là câu rất dài dùng để buộc estimator muốn rewrite nhưng chunk này phải được reuse nguyên trạng",
        "A",
        duration_ms=670,
    )
    _seed_reusable_chunk(
        work,
        2,
        "đây cũng là câu dài để chunk mục tiêu được phép rewrite trước khi regenerate",
        "B",
        duration_ms=1550,
    )

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    async def fake_duration(path):
        return 400

    generated = []
    synth_calls = []

    async def fake_generate(text, *, output_path, **kwargs):
        generated.append(output_path.name)
        synth_calls.append(text)
        output_path.write_bytes(b"new")
        # Chunk 2 is the regeneration target, so its MEASURED duration is what
        # authorises a rewrite; since Y1 the estimate no longer can. The first
        # synthesis overflows chunk 2's ~1150 ms allocated window (>1.30x) but
        # stays under emergency_speed_max. Once the rewrite has shortened the
        # text the next synthesis fits, so the loop converges in one rewrite.
        return 1500 if len(synth_calls) == 1 else 900

    adapted = []
    async def fake_adapt(ai, *, plan, chunk, segment_by_id, **kwargs):
        adapted.append(chunk.id)
        for idx in chunk.item_indexes:
            plan[idx].spoken_text = "ngắn"
            plan[idx].estimated_duration_ms = 300
            segment_by_id[plan[idx].segment_id].translated_text = "ngắn"
        return True

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_audio_duration_ms", fake_duration)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_adapt_chunk_items", fake_adapt)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 2.0,
    )

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_rewrite_attempts=2, dub_speed_max=1.30),
        user_settings=object(),
        work_dir=work,
        regenerate_chunk_ids={2},
        reuse_existing_chunks=True,
    )

    assert adapted == [2]
    # Chunk 2 is synthesized once before the measured-overflow rewrite and once
    # after it; chunk 1 is reused and never synthesized.
    assert [name.split(".pending", 1)[0] for name in generated] == [
        "chunk_0002", "chunk_0002",
    ]
    assert len(set(generated)) == len(generated), "Every generation owns a distinct pending path"
    assert all(name.endswith(".wav") for name in generated)
    assert (work / "chunk_0002.wav").read_bytes() == b"new"
    assert result.transcript.segments[0].translated_text.startswith("đây là câu rất dài")
    assert result.transcript.segments[1].translated_text == "ngắn"


@pytest.mark.asyncio
async def test_c7_resumed_reused_overlong_chunk_can_still_rewrite(
    monkeypatch, tmp_path,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text="bản dịch dài",
            source_start_ms=0,
            source_end_ms=500,
            translation_status="success",
        )
    ])
    work = tmp_path / "work"
    work.mkdir()
    _seed_reusable_chunk(work, 1, "bản dịch dài", "A")

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    async def fake_duration(path):
        return 2000

    adapted = []
    async def fake_adapt(ai, *, plan, chunk, segment_by_id, **kwargs):
        adapted.append(chunk.id)
        for idx in chunk.item_indexes:
            plan[idx].spoken_text = "ngắn"
            plan[idx].estimated_duration_ms = 200
            segment_by_id[plan[idx].segment_id].translated_text = "ngắn"
        return True

    async def fake_generate(text, *, output_path, **kwargs):
        output_path.write_bytes(b"fresh")
        return 400

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_audio_duration_ms", fake_duration)
    monkeypatch.setattr(ls, "_adapt_chunk_items", fake_adapt)
    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 1.0,
    )

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_rewrite_attempts=2, dub_speed_max=1.30),
        user_settings=object(),
        work_dir=work,
        reuse_existing_chunks=True,
    )

    assert adapted == [1]
    assert result.transcript.segments[0].translated_text == "ngắn"
    assert (work / "chunk_0001.wav").read_bytes() == b"fresh"



@pytest.mark.asyncio
async def test_c7_stale_raw_chunk_identity_forces_regeneration(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            speaker_id="A",
            source_text="one",
            translated_text="văn bản mới",
            source_start_ms=0,
            source_end_ms=1200,
            translation_status="success",
        )
    ])
    work = tmp_path / "work"
    work.mkdir()
    _seed_reusable_chunk(work, 1, "văn bản cũ", "A")

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())

    generated = []

    async def fake_generate(text, *, output_path, **kwargs):
        generated.append(text)
        output_path.write_bytes(b"fresh")
        return 500

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_chunk", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 2.0,
    )

    await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(),
        user_settings=object(),
        work_dir=work,
        reuse_existing_chunks=True,
    )

    assert generated == ["văn bản mới"]
    assert (work / "chunk_0001.wav").read_bytes() == b"fresh"


@pytest.mark.asyncio
async def test_atomic_chunk_regeneration_preserves_previous_audio_on_failure(
    monkeypatch, tmp_path,
):
    target = tmp_path / "chunk_0001.wav"
    target.write_bytes(b"previous-good-audio")

    async def fail_generate(text, *, output_path, **kwargs):
        output_path.write_bytes(b"partial-bad-audio")
        raise RuntimeError("tts provider failed")

    monkeypatch.setattr(ls, "_generate_tts_chunk", fail_generate)

    with pytest.raises(RuntimeError, match="tts provider failed"):
        await ls._generate_tts_chunk_atomic(
            "new text",
            output_path=target,
            identity="new-identity",
            runtime=_runtime(),
            voice="vi",
            profile=_profile(),
            speaker_id="A",
            target_language="Vietnamese",
        )

    assert target.read_bytes() == b"previous-good-audio"
    assert {path.name for path in tmp_path.iterdir()} == {target.name}, "Failed generation leaves no pending artifact"



@pytest.mark.asyncio
async def test_adapt_chunk_invalid_shape_records_qc_warning():
    from backend.services.dubbing_planner import DubbingCue, DubbingConfig, build_plan

    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="source",
            translated_text="bản dịch",
            source_start_ms=0,
            source_end_ms=1000,
            translation_status="success",
        )
    ])
    plan, chunks = build_plan(
        [DubbingCue(
            index=1,
            segment_id="s1",
            start_ms=0,
            end_ms=1000,
            text="bản dịch",
        )],
        DubbingConfig(),
        language="Vietnamese",
    )

    class BadShapeAI:
        async def chat(self, *args, **kwargs):
            return "[]"

    issues = []
    changed = await ls._adapt_chunk_items(
        BadShapeAI(),
        plan=plan,
        chunk=chunks[0],
        segment_by_id={"s1": tx.segments[0]},
        target_language="Vietnamese",
        target_ms=1000,
        profile=_profile(),
        issues=issues,
        state={"measured_duration_ms": 1800, "attempt": 1},
    )
    assert changed is False
    assert issues[0]["reason"] == "rewrite_invalid_response"
    assert issues[0]["detail"] == "shape_or_length"




@pytest.mark.asyncio
async def test_translation_retry_switches_to_saved_fallback_provider(monkeypatch):
    events = []

    class Transient503(RuntimeError):
        status_code = 503

    class Primary:
        connection_name = "compatible"
        provider = SimpleNamespace(value="compatible")
        model = "longcat"
        max_retries = None
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise Transient503("Hermes proxy unavailable")

    class Fallback:
        connection_name = "lmstudio"
        provider = SimpleNamespace(value="compatible")
        model = "qwen3:8b"
        max_retries = None
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return "fallback-ok"

    async def no_sleep(_delay):
        return None

    async def progress(event):
        events.append(dict(event))

    primary = Primary()
    fallback = Fallback()
    monkeypatch.setattr(ls.asyncio, "sleep", no_sleep)

    result = await ls._call_ai_chat_resilient(
        primary,
        fallback,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=8,
        max_attempts=2,
        attempt_timeout_s=1,
        total_timeout_s=5,
        progress_cb=progress,
        stage="translation",
    )

    assert result == "fallback-ok"
    assert primary.calls == 2
    assert fallback.calls == 1
    assert primary.max_retries == 0
    assert fallback.max_retries == 0
    assert any(e["event"] == "switch_fallback" and e["provider"] == "lmstudio" for e in events)
    # N-route taxonomy: the first fallback route is index 1, labelled
    # "fallback-1". Assert the role set rather than a single literal, so the
    # assertion holds for any route count.
    attempt_roles = [e["role"] for e in events if e["event"] == "attempt"]
    assert "primary" in attempt_roles
    assert "fallback-1" in attempt_roles


@pytest.mark.asyncio
async def test_translation_hard_deadline_cancels_hanging_ai():
    class Hanging:
        connection_name = "hermes"
        provider = SimpleNamespace(value="compatible")
        model = "slow"
        max_retries = None

        async def chat(self, **kwargs):
            await __import__("asyncio").Event().wait()

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        await ls._call_ai_chat_resilient(
            Hanging(),
            None,
            messages=[{"role": "user", "content": "x"}],
            max_tokens=8,
            max_attempts=2,
            attempt_timeout_s=0.01,
            total_timeout_s=0.03,
            stage="translation",
        )


def test_saved_inactive_local_provider_resolves_as_fallback():
    settings = SimpleNamespace(
        ai_provider="compatible",
        ai_model="longcat",
        ai_base_url="http://127.0.0.1:8645/v1",
        ai_api_key_encrypted=None,
        ai_provider_profiles_json=json.dumps({
            "lmstudio": {
                "model": "qwen3:8b",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_encrypted": None,
            },
        }),
    )
    profile = _profile(
        translation_fallback_provider="lmstudio",
        translation_fallback_model=None,
    )

    fallback = ls._profile_ai_fallback_client(
        settings,
        profile,
        "translation",
        primary_ai=None,
    )

    assert fallback.connection_name == "lmstudio"
    assert fallback.model == "qwen3:8b"
    assert fallback.base_url == "http://127.0.0.1:1234/v1"
    assert fallback.api_key == "local"



@pytest.mark.asyncio
async def test_hanging_primary_cannot_starve_translation_fallback():
    import asyncio

    class HangingPrimary:
        connection_name = "hermes"
        provider = SimpleNamespace(value="compatible")
        model = "longcat"
        max_retries = None

        async def chat(self, **kwargs):
            await asyncio.Event().wait()

    class FastFallback:
        connection_name = "lmstudio"
        provider = SimpleNamespace(value="compatible")
        model = "qwen3:8b"
        max_retries = None
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return "fallback-ok"

    fallback = FastFallback()
    events = []

    result = await ls._call_ai_chat_resilient(
        HangingPrimary(),
        fallback,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=8,
        max_attempts=3,
        attempt_timeout_s=0.05,
        total_timeout_s=0.08,
        progress_cb=lambda event: events.append(dict(event)),
        stage="translation",
    )

    assert result == "fallback-ok"
    assert fallback.calls == 1
    assert any(
        e.get("event") == "switch_fallback" and e.get("provider") == "lmstudio"
        for e in events
    )



def test_materialized_env_provider_can_resolve_again_without_saved_registry(monkeypatch):
    from backend.core.ai_provider import AIClient, AIProvider

    env_client = AIClient(
        provider=AIProvider.OPENAI,
        api_key="env-openai-key",
        model="env-model",
    )
    monkeypatch.setattr(
        "backend.core.ai_provider.get_ai_client",
        lambda settings=None: env_client,
    )

    settings = SimpleNamespace(
        ai_provider="",
        ai_model=None,
        ai_base_url=None,
        ai_api_key_encrypted=None,
        ai_provider_profiles_json="{}",
    )

    client = ls._ai_client_for_connection(
        settings,
        "openai",
        model="materialized-model",
    )

    assert client.api_key == "env-openai-key"
    assert client.model == "materialized-model"
    assert client.connection_name == "openai"



@pytest.mark.asyncio
async def test_three_route_chain_sticks_to_second_fallback_after_success():
    class Transient503(RuntimeError):
        status_code = 503

    class Client:
        def __init__(self, name, answer=None):
            self.connection_name = name
            self.provider = SimpleNamespace(value="compatible")
            self.model = name + "-model"
            self.max_retries = None
            self.answer = answer
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            if self.answer is None:
                raise Transient503(self.connection_name + " unavailable")
            return self.answer

    primary = Client("hermes-a")
    fallback1 = Client("hermes-b")
    fallback2 = Client("lmstudio", "ok")
    state = ls._AIStageRouteState([primary, fallback1, fallback2])

    first = await ls._call_ai_chat_resilient(
        primary,
        fallback1,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=8,
        max_attempts=1,
        attempt_timeout_s=0.1,
        total_timeout_s=1,
        route_state=state,
    )
    assert first == "ok"
    assert state.sticky_index == 2
    assert [primary.calls, fallback1.calls, fallback2.calls] == [1, 1, 1]

    second = await ls._call_ai_chat_resilient(
        primary,
        fallback1,
        messages=[{"role": "user", "content": "y"}],
        max_tokens=8,
        max_attempts=1,
        attempt_timeout_s=0.1,
        total_timeout_s=1,
        route_state=state,
    )
    assert second == "ok"
    assert [primary.calls, fallback1.calls, fallback2.calls] == [1, 1, 2]


@pytest.mark.asyncio
async def test_exhausted_route_chain_attaches_non_secret_failure_history():
    class Dead(RuntimeError):
        status_code = 503

    class Client:
        def __init__(self, name):
            self.connection_name = name
            self.provider = SimpleNamespace(value="compatible")
            self.model = name + "-model"
            self.max_retries = None

        async def chat(self, **kwargs):
            raise Dead(self.connection_name + " failed")

    clients = [Client("a"), Client("b"), Client("c")]
    state = ls._AIStageRouteState(clients)

    with pytest.raises(Dead) as exc:
        await ls._call_ai_chat_resilient(
            clients[0],
            clients[1],
            messages=[{"role": "user", "content": "x"}],
            max_tokens=8,
            max_attempts=1,
            attempt_timeout_s=0.1,
            total_timeout_s=1,
            route_state=state,
        )

    assert [x["connection_id"] for x in exc.value.ai_failure_chain] == ["a", "b", "c"]
    assert all("api_key" not in item for item in exc.value.ai_failure_chain)
    assert exc.value.ai_selected_connection == "c"
    assert exc.value.ai_sticky_route_index == 0
    assert exc.value.ai_attempt_count == 3
    assert exc.value.ai_success_count == 0



@pytest.mark.asyncio
async def test_invalid_structured_translation_advances_to_next_route():
    class BadStructured:
        connection_name = "hermes-main"
        provider = SimpleNamespace(value="compatible")
        model = "longcat"
        max_retries = None
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return '{"not":"the required array"}'

    class GoodFallback:
        connection_name = "lmstudio-qwen"
        provider = SimpleNamespace(value="compatible")
        model = "qwen3:8b"
        max_retries = None
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return json.dumps([
                {"id": "s1", "text": "xin chào"},
                {"id": "s2", "text": "thế giới"},
            ])

    batch = [
        LocalizationSegment(
            id="s1", source_text="hello", source_start_ms=0, source_end_ms=500,
        ),
        LocalizationSegment(
            id="s2", source_text="world", source_start_ms=500, source_end_ms=1000,
        ),
    ]
    primary = BadStructured()
    fallback = GoodFallback()
    state = ls._AIStageRouteState([primary, fallback])

    texts, failed = await ls._translate_batch_resilient(
        primary,
        batch,
        route_state=state,
        target_language="Vietnamese",
        context="hello world",
        profile=_profile(
            translation_retry_attempts=1,
            translation_attempt_timeout_s=10,
            translation_total_timeout_s=20,
        ),
        for_dub=True,
        fallback_ai=fallback,
    )

    assert texts == ["xin chào", "thế giới"]
    assert failed == set()
    assert primary.calls == 1
    assert fallback.calls == 1
    assert state.sticky_index == 1
    assert any(
        item.get("error_type") == "InvalidStructuredOutput"
        and item.get("connection_id") == "hermes-main"
        for item in state.failure_chain
    )


@pytest.mark.asyncio
async def test_translation_recursive_shape_split_shares_one_batch_deadline(monkeypatch):
    seen_deadlines = []

    async def fake_call(
        ai,
        fallback_ai,
        messages,
        max_tokens=3000,
        max_attempts=3,
        *,
        attempt_timeout_s=75,
        total_timeout_s=180,
        progress_cb=None,
        stage="translation",
        route_state=None,
        deadline_at=None,
        dispatch_budget=None,
    ):
        seen_deadlines.append(deadline_at)
        return "[]"

    monkeypatch.setattr(ls, "_call_ai_chat_resilient", fake_call)

    batch = [
        LocalizationSegment(
            id=f"s{i}",
            source_text=f"source {i}",
            source_start_ms=i * 500,
            source_end_ms=(i + 1) * 500,
        )
        for i in range(4)
    ]

    texts, failed = await ls._translate_batch_resilient(
        object(),
        batch,
        target_language="Vietnamese",
        context="ctx",
        profile=_profile(
            translation_attempt_timeout_s=10,
            translation_total_timeout_s=30,
        ),
        for_dub=True,
    )

    assert len(texts) == 4
    assert failed == {0, 1, 2, 3}
    assert len(seen_deadlines) > 1
    assert all(value is not None for value in seen_deadlines)
    first = seen_deadlines[0]
    assert all(value == first for value in seen_deadlines)



@pytest.mark.asyncio
async def test_ai_failure_provenance_redacts_api_key():
    events = []

    class SecretFailure:
        connection_name = "cloud-main"
        provider = SimpleNamespace(value="openai")
        model = "model-x"
        api_key = "super-secret-key"
        max_retries = None

        async def chat(self, **kwargs):
            raise RuntimeError("request failed with token super-secret-key")

    client = SecretFailure()
    state = ls._AIStageRouteState([client])

    with pytest.raises(RuntimeError):
        await ls._call_ai_chat_resilient(
            client,
            None,
            messages=[{"role": "user", "content": "x"}],
            max_attempts=1,
            attempt_timeout_s=1,
            total_timeout_s=2,
            progress_cb=lambda event: events.append(dict(event)),
            route_state=state,
        )

    serialized = json.dumps({
        "chain": state.failure_chain,
        "events": events,
    })
    assert "super-secret-key" not in serialized
    assert "***" in serialized


@pytest.mark.asyncio
async def test_eight_route_chain_reaches_last_healthy_fallback_without_starvation():
    class Transient503(RuntimeError):
        status_code = 503

    class Client:
        def __init__(self, name, answer=None):
            self.connection_name = name
            self.provider = SimpleNamespace(value="compatible")
            self.model = name + "-model"
            self.max_retries = None
            self.answer = answer
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            if self.answer is None:
                raise Transient503(self.connection_name + " unavailable")
            return self.answer

    clients = [Client(f"route-{i}") for i in range(7)] + [Client("route-7", "ok")]
    state = ls._AIStageRouteState(clients)

    result = await ls._call_ai_chat_resilient(
        clients[0],
        clients[1],
        messages=[{"role": "user", "content": "x"}],
        max_tokens=8,
        max_attempts=1,
        attempt_timeout_s=1,
        total_timeout_s=8,
        route_state=state,
    )

    assert result == "ok"
    assert state.sticky_index == 7
    assert [client.calls for client in clients] == [1] * 8
    assert [item["connection_id"] for item in state.failure_chain] == [
        f"route-{i}" for i in range(7)
    ]


def test_ai_failure_history_is_bounded_to_recent_events():
    state = ls._AIStageRouteState([SimpleNamespace(connection_name="only")])
    for index in range(100):
        ls._append_ai_failure(state, {
            "connection_id": "only",
            "attempt": index,
            "error": f"failure-{index}",
        })

    assert len(state.failure_chain) == ls._MAX_AI_FAILURE_HISTORY
    assert state.failure_chain[0]["attempt"] == 36
    assert state.failure_chain[-1]["attempt"] == 99


@pytest.mark.asyncio
async def test_translation_checkpoint_restores_sticky_fallback_and_audit(monkeypatch, tmp_path):
    class Transient503(RuntimeError):
        status_code = 503

    class Primary:
        connection_name = "primary"
        provider = SimpleNamespace(value="compatible")
        model = "primary-model"
        max_retries = None

        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise Transient503("primary unavailable")

    class Fallback:
        connection_name = "fallback"
        provider = SimpleNamespace(value="compatible")
        model = "fallback-model"
        max_retries = None

        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return json.dumps([{"id": "s1", "text": "xin chào"}])

    first_primary = Primary()
    first_fallback = Fallback()
    active_clients = [first_primary, first_fallback]
    monkeypatch.setattr(
        ls,
        "_profile_ai_route_clients_compat",
        lambda *_args, **_kwargs: active_clients,
    )

    profile = _profile(
        translation_routes=[
            {"connection_id": "primary", "model": "primary-model"},
            {"connection_id": "fallback", "model": "fallback-model"},
        ],
        translation_retry_attempts=1,
        translation_attempt_timeout_s=10,
        translation_total_timeout_s=20,
    )
    transcript = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1",
            source_text="hello",
            source_start_ms=0,
            source_end_ms=500,
        ),
    ])

    first = await ls.translate_transcript(
        transcript,
        target_language="Vietnamese",
        profile=profile,
        user_settings=SimpleNamespace(
            ai_connection_id="primary",
            ai_provider="compatible",
        ),
        for_dub=True,
        checkpoint_dir=tmp_path,
    )
    assert first.segments[0].translated_text == "xin chào"
    assert first.metadata["translation_ai"]["selected_connection_id"] == "fallback"
    assert first.metadata["translation_ai"]["attempted"] is True
    assert [first_primary.calls, first_fallback.calls] == [1, 1]

    resumed_primary = Primary()
    resumed_fallback = Fallback()
    active_clients = [resumed_primary, resumed_fallback]

    resumed = await ls.translate_transcript(
        transcript,
        target_language="Vietnamese",
        profile=profile,
        user_settings=SimpleNamespace(
            ai_connection_id="primary",
            ai_provider="compatible",
        ),
        for_dub=True,
        checkpoint_dir=tmp_path,
    )

    assert [resumed_primary.calls, resumed_fallback.calls] == [0, 0]
    audit = resumed.metadata["translation_ai"]
    assert audit["selected_connection_id"] == "fallback"
    assert audit["sticky_route_index"] == 1
    assert audit["attempted"] is True
    assert audit["attempt_count"] == 2
    assert audit["success_count"] == 1
    assert audit["failure_chain"][0]["connection_id"] == "primary"


@pytest.mark.asyncio
async def test_c7_timing_failure_reports_last_attempted_route_when_all_rewrite_routes_fail(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=500, translation_status="success",
        ),
    ])
    _patch_dub_io(monkeypatch, tmp_path, [2000])
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 0.5,
    )

    primary = SimpleNamespace(connection_name="hermes-main")
    fallback = SimpleNamespace(connection_name="lmstudio-qwen")
    monkeypatch.setattr(
        ls,
        "_profile_ai_route_clients_compat",
        lambda *_args, **_kwargs: [primary, fallback],
    )

    async def all_routes_failed_without_fit(*args, route_state=None, **kwargs):
        if route_state is not None:
            route_state.failure_chain = [
                {"connection_id": "hermes-main", "error_type": "TimeoutError", "error": "down"},
                {"connection_id": "lmstudio-qwen", "error_type": "ConnectionError", "error": "down"},
            ]
            # Transport exhaustion does not make any failed route sticky.
            route_state.sticky_index = 0
            route_state.attempt_count = 2
            route_state.success_count = 0
        return False

    monkeypatch.setattr(ls, "_adapt_chunk_items", all_routes_failed_without_fit)

    with pytest.raises(ls.LocalizationTimingError) as exc:
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(
                dub_rewrite_attempts=1,
                dub_speed_max=1.30,
                video_retime_mode="off",
            ),
            user_settings=object(),
            work_dir=tmp_path / "work",
        )

    audit = exc.value.rewrite_ai
    assert audit["selected_connection_id"] == "lmstudio-qwen"
    assert audit["sticky_route_index"] == 0
    assert audit["attempted"] is True
    assert audit["success_count"] == 0


@pytest.mark.asyncio
async def test_all_malformed_translation_routes_attach_terminal_route_audit(monkeypatch):
    class InvalidClient:
        provider = SimpleNamespace(value="compatible")
        max_retries = None

        def __init__(self, name):
            self.connection_name = name
            self.model = name + "-model"
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return '{"not":"a translation array"}'

    primary = InvalidClient("hermes-main")
    fallback = InvalidClient("lmstudio-qwen")
    monkeypatch.setattr(
        ls,
        "_profile_ai_route_clients_compat",
        lambda *_args, **_kwargs: [primary, fallback],
    )

    profile = _profile(
        translation_routes=[
            {"connection_id": "hermes-main", "model": "hermes-main-model"},
            {"connection_id": "lmstudio-qwen", "model": "lmstudio-qwen-model"},
        ],
        translation_retry_attempts=1,
        translation_attempt_timeout_s=10,
        translation_total_timeout_s=20,
    )
    transcript = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="hello",
            source_start_ms=0, source_end_ms=500,
        ),
    ])

    with pytest.raises(RuntimeError, match="failed for every segment") as exc:
        await ls.translate_transcript(
            transcript,
            target_language="Vietnamese",
            profile=profile,
            user_settings=SimpleNamespace(ai_provider="compatible"),
            for_dub=True,
        )

    assert primary.calls == 1
    assert fallback.calls == 1
    assert [item["connection_id"] for item in exc.value.ai_failure_chain] == [
        "hermes-main", "lmstudio-qwen",
    ]
    assert exc.value.ai_selected_connection == "lmstudio-qwen"
    assert exc.value.ai_attempt_count == 2
    assert exc.value.ai_success_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_ms", [1000, 1150, 1250])
@pytest.mark.parametrize("legacy", [False, True])
async def test_targeted_retry_preserves_measured_non_target_windows(
    monkeypatch, tmp_path, first_ms, legacy,
):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="a", speaker_id="A", source_text="first",
            translated_text="Giữ lời đầu.", source_start_ms=0, source_end_ms=800,
            translation_status="success"),
        LocalizationSegment(id="b", speaker_id="B", source_text="second",
            translated_text="Câu thứ hai dài hơn nhiều.", source_start_ms=1400,
            source_end_ms=1800, translation_status="success"),
    ])
    original = tx.model_dump()
    calls = _patch_dub_io(monkeypatch, tmp_path, [first_ms, 300, 200, 500])
    profile = _profile(dub_rewrite_attempts=0)
    work = tmp_path / "work"
    async def run_dub(targets=None):
        return await ls.synthesize_timed_dub(
            tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
            profile=profile, user_settings=object(), work_dir=work,
            regenerate_chunk_ids=targets, reuse_existing_chunks=bool(targets),
        )

    full = await run_dub()
    assert full.chunks
    frozen = (full.chunks[0].start_ms, full.chunks[0].end_ms)
    assert frozen[1] > 800, "fixture must reclaim actual, not estimated, slack"
    raw = work / "chunk_0001.wav"
    before = (raw.read_bytes(), raw.stat().st_mtime_ns)
    if legacy:
        state_path = work / "dubbing_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("measured_allocation", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
    for _ in range(2):
        retry = await run_dub({2})
        assert retry.chunks
        assert (retry.chunks[0].start_ms, retry.chunks[0].end_ms) == frozen
        assert retry.chunks[0].end_ms <= retry.chunks[1].start_ms
        assert (raw.read_bytes(), raw.stat().st_mtime_ns) == before
        assert tx.model_dump() == original
    assert calls["n"] == 4, "only the requested chunk may regenerate"


def _pressure_transcript(count=12):
    return LocalizationTranscript(language="en", segments=[LocalizationSegment(
        id=f"p{i}", source_text="Do not move." if i == 7 else "Hello.",
        source_start_ms=i * 1000, source_end_ms=(i + 1) * 1000,
    ) for i in range(count)])


class _PartialTranslationAI:
    connection_name = "qa-partial"
    provider = SimpleNamespace(value="compatible")
    model = "qa-model"
    base_url = "http://localhost:9655/v1"
    max_retries = 0

    def __init__(self, *, repair=False, crash=False):
        self.repair = repair
        self.crash = crash
        self.ids = []

    async def chat(self, messages, **kwargs):
        prompt = messages[0]["content"]
        items = json.loads(prompt.split("SEGMENTS:\n", 1)[1].split("\n\nReturn ONLY", 1)[0])
        self.ids.append([x["id"] for x in items])
        if self.crash and len(self.ids) == 2:
            raise RuntimeError("provider unavailable during repair")
        return json.dumps([{"id": x["id"], "text": (
            "Đừng di chuyển." if self.repair and x["id"] == "p7"
            else "Di chuyển." if x["id"] == "p7" else "Xin chào."
        )} for x in items], ensure_ascii=False)


@pytest.mark.asyncio
async def test_semantic_repair_only_sends_rejected_id(monkeypatch):
    ai = _PartialTranslationAI(repair=True)
    original = ai.chat

    async def first_bad_then_good(*args, **kwargs):
        ai.repair = bool(ai.ids)
        return await original(*args, **kwargs)

    ai.chat = first_bad_then_good
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: ai)
    result = await ls.translate_transcript(_pressure_transcript(), target_language="vi",
        profile=_profile(glossary={}, qc_profile="strict"), user_settings=object(), for_dub=True)
    assert ai.ids == [[f"p{i}" for i in range(12)], ["p7"]]
    assert all(x.translation_status == "success" for x in result.segments)
    assert result.segments[7].translated_text == "Đừng di chuyển."


@pytest.mark.asyncio
@pytest.mark.parametrize("crash", [False, True])
async def test_partial_checkpoint_survives_semantic_or_transport_failure(monkeypatch, tmp_path, crash):
    ai = _PartialTranslationAI(crash=crash)
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: ai)
    profile = _profile(glossary={}, qc_profile="strict", translation_retry_attempts=1)
    kwargs = dict(target_language="vi", profile=profile, user_settings=object(),
                  for_dub=True, checkpoint_dir=tmp_path)
    with pytest.raises(RuntimeError):
        await ls.translate_transcript(_pressure_transcript(), **kwargs)
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    saved = checkpoint["batches"]["0"]
    assert [x["id"] for x in saved["items"]] == [f"p{i}" for i in range(12) if i != 7]
    assert saved["pending_ids"] == ["p7"]
    assert ai.ids == [[f"p{i}" for i in range(12)], ["p7"]]
    accepted_before = saved["items"]
    repaired = _PartialTranslationAI(repair=True)
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: repaired)
    result = await ls.translate_transcript(_pressure_transcript(), **kwargs)
    assert repaired.ids == [["p7"]]
    assert all(x.translation_status == "success" for x in result.segments)
    after = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))["batches"]["0"]
    assert [x for x in after["items"] if x["id"] != "p7"] == accepted_before
    assert after["pending_ids"] == []


@pytest.mark.asyncio
async def test_malformed_batches_split_once_inside_original_budget():
    class Malformed:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return "not JSON"

    ai = Malformed()
    texts, failed = await ls._translate_batch_resilient(ai, _pressure_transcript().segments,
        target_language="vi", source_language="en", context="context",
        profile=_profile(glossary={}), for_dub=True)
    assert ai.calls == 3  # initial + two halves, no singleton fan-out
    assert failed == set(range(12))
    assert len(texts) == 12


@pytest.mark.asyncio
async def test_transport_and_shape_retries_share_four_dispatch_budget(monkeypatch):
    class Transient(RuntimeError):
        status_code = 503

    class Failing:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            if self.calls % 2:
                raise Transient("transport unavailable")
            return "not JSON"

    async def no_sleep(_):
        pass

    monkeypatch.setattr(ls.asyncio, "sleep", no_sleep)
    ai = Failing()
    with pytest.raises(RuntimeError, match="dispatch budget"):
        await ls._translate_batch_resilient(ai, _pressure_transcript().segments,
            target_language="vi", context="context", profile=_profile(glossary={},
                translation_retry_attempts=3), for_dub=True)
    assert ai.calls == 4


@pytest.mark.asyncio
async def test_name_conflict_repairs_all_dependent_items_but_keeps_independent():
    batch = [LocalizationSegment(id=f"n{i}", source_text=source,
        source_start_ms=i * 1000, source_end_ms=(i + 1) * 1000)
        for i, source in enumerate(["张三来了", "张三走了", "你好"])]

    class Names:
        ids = []

        async def chat(self, messages, **kwargs):
            items = json.loads(messages[0]["content"].split("SEGMENTS:\n", 1)[1].split("\n\nReturn ONLY", 1)[0])
            self.ids.append([x["id"] for x in items])
            return json.dumps([{"id": x["id"], "text": (
                "Trương Tam đến." if x["id"] == "n0" else "Trương Ba đi." if x["id"] == "n1" else "Xin chào."
            ), "localized_terms": {"张三": "Trương Tam" if x["id"] == "n0" else "Trương Ba"}
                if x["id"] != "n2" else {}} for x in items], ensure_ascii=False)

    ai = Names()
    terms = {}
    texts, failed = await ls._translate_batch_resilient(ai, batch,
        source_language="zh", target_language="vi", context="context",
        profile=_profile(glossary={}), for_dub=True, localized_terms=terms)
    assert ai.ids == [["n0", "n1", "n2"], ["n0", "n1"]]
    assert failed == {0, 1}
    assert texts[2] == "Xin chào."
    assert terms == {}  # conflicting names must never be committed arbitrarily
