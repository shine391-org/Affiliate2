# SPDX-License-Identifier: AGPL-3.0-only
"""Phase C6-C8 localization engine regressions."""
from __future__ import annotations

import asyncio
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
    )
    return LocalizationProfile.model_validate({**base.model_dump(), **updates})


def _transcript():
    segs = [
        LocalizationSegment(
            id="s1", source_text="第一句", source_start_ms=0, source_end_ms=1000,
        ),
        LocalizationSegment(
            id="s2", source_text="品牌很好", source_start_ms=1200, source_end_ms=2200,
        ),
    ]
    return LocalizationTranscript(
        text="第一句 品牌很好",
        language="zh",
        source="whisper",
        segments=segs,
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
    assert all(s.target_start_ms is None for s in tx.segments)


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
    assert ai.calls == 3  # bad pair -> two single-item retries
    assert [s.translated_text for s in tx.segments] == ["vi:第一句", "vi:品牌很好"]
    assert [(s.target_start_ms, s.target_end_ms) for s in tx.segments] == [
        (0, 1000), (1200, 2200)
    ]


@pytest.mark.asyncio
async def test_c7_measured_overflow_adapts_text_before_speed(monkeypatch, tmp_path):
    tx = _transcript()
    for seg in tx.segments:
        seg.translated_text = "too long"
        seg.translation_status = "success"

    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: ("edge", "voice", "", None))

    calls = []
    async def fake_generate(text, *, speed, output_path, **kwargs):
        calls.append((text, speed))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return 800 if text == "short" else 1500

    async def fake_adapt(ai, seg, **kwargs):
        return "short"

    def fake_compose(blocks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_block", fake_generate)
    monkeypatch.setattr(ls, "_adapt_translation_for_duration", fake_adapt)
    monkeypatch.setattr(ls, "_compose_dub_track_sync", fake_compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 3.0)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(timing_tolerance_ms=0),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert all(s.translated_text == "short" for s in result.transcript.segments)
    assert all(s.tts_duration_ms == 800 for s in result.transcript.segments)
    assert all(s.timing_status == "adjusted" for s in result.transcript.segments)
    assert result.issues == []
    assert calls[0] == ("too long", 1.0)
    assert calls[1] == ("short", 1.0)


@pytest.mark.asyncio
async def test_c7_safe_rate_is_bounded_and_measured(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        text="x",
        segments=[LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=1000,
            translation_status="success",
        )],
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: ("edge", "voice", "", None))
    monkeypatch.setattr(ls, "_adapt_translation_for_duration", lambda *a, **k: asyncio.sleep(0, result="dịch"))

    speeds = []
    async def fake_generate(text, *, speed, output_path, **kwargs):
        speeds.append(speed)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return 1080 if speed == 1.0 else 990

    def fake_compose(blocks, *, total_duration_ms, output_path):
        output_path.write_bytes(b"wav")
        return output_path

    monkeypatch.setattr(ls, "_generate_tts_block", fake_generate)
    monkeypatch.setattr(ls, "_compose_dub_track_sync", fake_compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 1.0)

    result = await ls.synthesize_timed_dub(
        tx,
        source_media=tmp_path / "source.mp4",
        target_language="Vietnamese",
        profile=_profile(timing_tolerance_ms=0, tts_rate_policy="auto"),
        user_settings=object(),
        work_dir=tmp_path / "work",
    )
    assert len(speeds) == 2
    assert speeds[0] == 1.0
    assert 1.0 < speeds[1] <= 1.12
    assert result.transcript.segments[0].tts_duration_ms == 990


@pytest.mark.asyncio
async def test_c7_unresolved_overflow_is_explicit(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        text="x",
        segments=[LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=500,
            translation_status="success",
        )],
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *_: ("edge", "voice", "", None))
    monkeypatch.setattr(ls, "_adapt_translation_for_duration", lambda *a, **k: asyncio.sleep(0, result="dịch"))

    async def too_long(text, *, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return 1500

    monkeypatch.setattr(ls, "_generate_tts_block", too_long)
    with pytest.raises(ls.LocalizationTimingError) as exc:
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(timing_tolerance_ms=0, tts_rate_policy="strict"),
            user_settings=object(),
            work_dir=tmp_path / "work",
        )
    assert exc.value.issues[0]["reason"] == "timing_overflow"


def test_c7_captions_use_final_target_timing_not_source_words():
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
    got = ls.caption_segments_from_localization(tx)
    assert got == [{"start": 1.2, "end": 2.3, "text": "tiếng Việt"}]
    assert "words" not in got[0]


def test_c8_mix_ducks_source_and_stream_copies_video(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    dub = tmp_path / "dub.wav"
    out = tmp_path / "out.mp4"
    source.write_bytes(b"video")
    dub.write_bytes(b"audio")

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
    got = ls._mix_dub_with_video_sync(
        source, dub, out,
        preserve_background=True,
        background_gain_db=-6.0,
        dub_gain_db=0.0,
    )
    cmd_text = " ".join(captured["cmd"])
    assert got == out
    assert "sidechaincompress" in cmd_text
    assert "-c:v copy" in cmd_text
    assert "-b:a 192k" in cmd_text


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
    assert str(provider.value) == "edge_tts"
    assert voice == "vi-VN-HoaiMyNeural"
    assert key == ""


@pytest.mark.asyncio
async def test_c6_strict_qc_rejects_any_untranslated_segment(monkeypatch):
    class _BadOneAI(_EchoAI):
        async def chat(self, messages, **kwargs):
            self.calls += 1
            prompt = messages[0]["content"]
            raw = prompt.split("SEGMENTS:\n", 1)[1].split("\n\nReturn ONLY", 1)[0]
            items = json.loads(raw)
            if len(items) == 1 and items[0]["id"] == "s2":
                return "not json"
            if any(x["id"] == "s2" for x in items):
                return "not json"
            return json.dumps(
                [{"id": x["id"], "text": f"vi:{x['text']}"} for x in items],
                ensure_ascii=False,
            )

    ai = _BadOneAI(lambda *_: None)
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: ai)
    with pytest.raises(RuntimeError, match="untranslated under strict QC"):
        await ls.translate_transcript(
            _transcript(),
            target_language="Vietnamese",
            profile=_profile(qc_profile="strict"),
            user_settings=object(),
            for_dub=False,
        )


@pytest.mark.asyncio
async def test_c7_strict_timing_ignores_tolerance_borrow(monkeypatch, tmp_path):
    tx = LocalizationTranscript(
        text="x",
        segments=[LocalizationSegment(
            id="s1", source_text="x", translated_text="dịch",
            source_start_ms=0, source_end_ms=500,
            translation_status="success",
        )],
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *_: object())
    monkeypatch.setattr(ls, "_tts_config", lambda *a, **k: ("edge", "voice", "", None))
    monkeypatch.setattr(
        ls, "_adapt_translation_for_duration",
        lambda *a, **k: asyncio.sleep(0, result="dịch"),
    )

    async def seven_hundred(text, *, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return 700

    monkeypatch.setattr(ls, "_generate_tts_block", seven_hundred)
    with pytest.raises(ls.LocalizationTimingError):
        await ls.synthesize_timed_dub(
            tx,
            source_media=tmp_path / "source.mp4",
            target_language="Vietnamese",
            profile=_profile(
                timing_policy="strict",
                timing_tolerance_ms=500,
                tts_rate_policy="strict",
            ),
            user_settings=object(),
            work_dir=tmp_path / "work",
        )
