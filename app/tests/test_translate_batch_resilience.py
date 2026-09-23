# SPDX-License-Identifier: AGPL-3.0-only
"""Phase C6R-C8R localization regressions."""
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
    )
    return LocalizationProfile.model_validate({**base.model_dump(), **updates})


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
    assert all(s.translation_status == "success" for s in tx.segments)


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


@pytest.mark.asyncio
async def test_c7_short_whisper_cues_become_one_tts_chunk(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        text="Yes and then continue",
        segments=[
            LocalizationSegment(
                id="s1", source_text="Yes.", translated_text="Có.",
                source_start_ms=0, source_end_ms=480,
                translation_status="success",
            ),
            LocalizationSegment(
                id="s2", source_text="and then", translated_text="và sau đó",
                source_start_ms=500, source_end_ms=1000,
                translation_status="success",
            ),
            LocalizationSegment(
                id="s3", source_text="continue", translated_text="tiếp tục",
                source_start_ms=1020, source_end_ms=1750,
                translation_status="success",
            ),
        ],
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: ("edge", "vi", "", None))

    generated = []
    async def fake_generate(text, *, output_path, **kwargs):
        generated.append(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        return 1800

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_block", fake_generate)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 2.0)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_speed_max=1.30),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert result.chunk_count == 1
    assert len(generated) == 1
    assert "Có." in generated[0] and "và sau đó" in generated[0]
    assert result.max_speed_factor < 1.1
    assert all(seg.target_start_ms is not None for seg in result.transcript.segments)


@pytest.mark.asyncio
async def test_c7_overlong_chunk_rewrites_then_regenerates(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        segments=[
            LocalizationSegment(
                id="s1", source_text="one", translated_text="một câu rất dài",
                source_start_ms=0, source_end_ms=900,
                translation_status="success",
            ),
            LocalizationSegment(
                id="s2", source_text="two", translated_text="thêm một câu dài",
                source_start_ms=950, source_end_ms=2000,
                translation_status="success",
            ),
        ],
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: ("edge", "vi", "", None))

    calls = {"n": 0}
    async def fake_generate(text, *, output_path, **kwargs):
        calls["n"] += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        return 4000 if calls["n"] == 1 else 2200

    async def fake_adapt(ai, *, plan, chunk, segment_by_id, **kwargs):
        for idx in chunk.item_indexes:
            plan[idx].spoken_text = "ngắn"
            segment_by_id[plan[idx].segment_id].translated_text = "ngắn"
        return True

    def fake_fit(source, destination, speed_factor):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fit")
        return destination

    def fake_compose(chunks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_block", fake_generate)
    monkeypatch.setattr(ls, "_adapt_chunk_items", fake_adapt)
    monkeypatch.setattr(ls, "_fit_chunk_audio_sync", fake_fit)
    monkeypatch.setattr(ls, "_compose_chunk_track_sync", fake_compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 2.0)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(dub_speed_max=1.30, dub_rewrite_attempts=2),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert calls["n"] == 2
    assert result.rewrite_count == 1
    assert result.max_speed_factor == pytest.approx(1.1, rel=1e-3)
    assert all(seg.translated_text == "ngắn" for seg in result.transcript.segments)


@pytest.mark.asyncio
async def test_c7_over_max_after_rewrite_fails_explicitly(monkeypatch, tmp_path):
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=500,
            translation_status="success",
        ),
    ])
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: ("edge", "vi", "", None))

    async def too_long(text, *, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"raw")
        return 2000

    async def no_change(*args, **kwargs):
        return False

    monkeypatch.setattr(ls, "_generate_tts_block", too_long)
    monkeypatch.setattr(ls, "_adapt_chunk_items", no_change)

    with pytest.raises(ls.LocalizationTimingError) as exc:
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(dub_speed_max=1.30),
            user_settings=object(),
            work_dir=tmp_path / "work",
        )
    assert exc.value.issues[0]["reason"] == "timing_overflow"
    assert exc.value.issues[0]["required_speed"] == 4.0


def test_c7_captions_use_fitted_target_timing():
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id="s1",
        source_text="source",
        translated_text="tiếng Việt",
        source_start_ms=1000,
        source_end_ms=2000,
        target_start_ms=1200,
        target_end_ms=2300,
        source_words=[{"word": "source", "start": 1.0, "end": 2.0}],
        translation_status="success",
        timing_status="adjusted",
    )])
    assert ls.caption_segments_from_localization(tx) == [
        {"start": 1.2, "end": 2.3, "text": "tiếng Việt"}
    ]


def test_c8_hq_clean_mix_uses_background_stem_and_stream_copies_video(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    dub = tmp_path / "dub.wav"
    bgm = tmp_path / "bgm.wav"
    out = tmp_path / "out.mp4"
    for path in (source, dub, bgm):
        path.write_bytes(b"x")

    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"mixed")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ls.subprocess, "run", fake_run)
    got = ls._mix_dub_with_video_sync(
        source, dub, out,
        audio_mode="hq_clean",
        background_file=bgm,
        preserve_background=True,
        background_gain_db=-6.0,
        dub_gain_db=0.0,
    )
    command = " ".join(captured["cmd"])
    assert got == out
    assert str(bgm) in command
    assert "sidechaincompress" not in command
    assert "alimiter=limit=0.95" in command
    assert "-c:v copy" in command


def test_c8_fast_duck_remains_explicit_fallback(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    dub = tmp_path / "dub.wav"
    out = tmp_path / "out.mp4"
    source.write_bytes(b"x")
    dub.write_bytes(b"x")

    monkeypatch.setattr(
        "backend.services.ffmpeg_service.has_audio_stream_sync",
        lambda _: True,
    )
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"mixed")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ls.subprocess, "run", fake_run)
    ls._mix_dub_with_video_sync(
        source, dub, out,
        audio_mode="fast_duck",
        background_file=None,
        preserve_background=True,
        background_gain_db=-6.0,
        dub_gain_db=0.0,
    )
    assert "sidechaincompress" in " ".join(captured["cmd"])


def test_c7_vietnamese_edge_default_uses_vietnamese_voice():
    settings = SimpleNamespace(
        tts_provider="edge_tts",
        preferred_tts_voice=None,
        preferred_voice_id=None,
        ai_provider=None,
        ai_api_key_encrypted=None,
    )
    provider, voice, key, model = ls._tts_config(
        settings, _profile(tts_voice=None), "Vietnamese"
    )
    assert provider.value == "edge_tts"
    assert voice == "vi-VN-HoaiMyNeural"
    assert key == ""


def test_profile_named_compatible_preset_must_match_settings(monkeypatch):
    from backend.core.ai_provider import AIProvider

    fake_ai = SimpleNamespace(provider=AIProvider.COMPATIBLE, model="x")
    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda settings: fake_ai)
    settings = SimpleNamespace(ai_provider="lmstudio")
    with pytest.raises(ValueError, match="requests AI provider 'ollama'"):
        ls._profile_ai_client(settings, _profile(translation_provider="ollama"))


def test_generic_compatible_profile_can_use_current_compatible_endpoint(monkeypatch):
    from backend.core.ai_provider import AIProvider

    fake_ai = SimpleNamespace(provider=AIProvider.COMPATIBLE, model="old")
    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda settings: fake_ai)
    settings = SimpleNamespace(ai_provider="lmstudio")
    got = ls._profile_ai_client(
        settings,
        _profile(translation_provider="compatible", translation_model="new-model"),
    )
    assert got is fake_ai
    assert got.model == "new-model"
