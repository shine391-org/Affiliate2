# SPDX-License-Identifier: AGPL-3.0-only
"""Phase C5-C8 donor-derived localization regressions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
            [{"id": x["id"], "text": f"vi:{x['text']}"} for x in items],
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
    async def fake_align(path, transcript, profile, **kwargs):
        seen["path"] = Path(path)
        seen["alignment"] = profile.alignment_mode
        return transcript, SimpleNamespace(
            alignment_applied=True,
            diarization_applied=False,
            diarization_reason="missing_huggingface_token",
            speaker_count=0,
            device="cpu",
        )

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
    assert [s.translated_text for s in tx.segments] == ["vi:第一句", "vi:品牌很好"]


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
    assert [s.translated_text for s in tx.segments] == ["vi:第一句", "vi:品牌很好"]


def _patch_dub_io(monkeypatch, tmp_path, durations, generated=None):
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: _runtime())
    calls = {"n": 0}

    async def fake_generate(text, *, output_path, voice, **kwargs):
        calls["n"] += 1
        if generated is not None:
            generated.append((text, voice, output_path.name))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        index = min(calls["n"] - 1, len(durations) - 1)
        return durations[index]

    def fake_fit(source, destination, speed_factor):
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
async def test_c7_short_whisper_cues_become_one_tts_chunk(monkeypatch, tmp_path):
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
    calls = _patch_dub_io(monkeypatch, tmp_path, [1800], generated)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_speed_max=1.30),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert result.chunk_count == 1
    assert calls["n"] == 1
    assert "Có." in generated[0][0] and "và sau đó" in generated[0][0]
    assert result.max_speed_factor < 1.1
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
    (work / "chunk_0001.wav").write_bytes(b"old1")
    (work / "chunk_0002.wav").write_bytes(b"old2")

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
    assert generated == ["chunk_0002.wav"]


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
    assert got.translation_base_url == "http://localhost:1234/v1"
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
        # Measured TTS happens to fit, proving rewrite timeout is not fatal.
        return 450

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

    assert slow.calls == 1
    assert result.chunk_count == 1
    assert any(
        issue["reason"] == "rewrite_ai_unavailable"
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
    state = {}
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
    assert state["unavailable"] is True
    assert issues[0]["reason"] == "rewrite_ai_unavailable"
    assert issues[0]["error_type"] == "TimeoutError"
