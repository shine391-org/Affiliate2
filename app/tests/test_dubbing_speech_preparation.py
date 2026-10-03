# SPDX-License-Identifier: AGPL-3.0-only
"""C6 localizes names before review; C7 never silently transliterates them."""
import asyncio
import json
import wave

import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider


def tx(text="Ngô Tiểu Đồng 18 tuổi!", source="吴小彤十八岁！"):
    return LocalizationTranscript(language="zh", segments=[LocalizationSegment(
        id="a", source_text=source, translated_text=text,
        source_start_ms=0, source_end_ms=3000, translation_status="success")])


def runtime(provider=TTSProvider.EDGE_TTS, voice="vi-VN-HoaiMyNeural"):
    return svc.TTSRuntimeConfig(provider, voice, "", None, None, None)


def mock_routes(monkeypatch):
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *_: [object()])
    monkeypatch.setattr(svc, "_profile_ai_route_specs", lambda *_: [])


def mock_audio(monkeypatch):
    monkeypatch.setattr(svc, "_tts_config", lambda *_: runtime())
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 3.0)
    spoken = []
    async def generate(text, *, output_path, **_kwargs):
        spoken.append(text)
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
            handle.writeframes(b"\x01\x00" * 16000)
        return 1000
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    return spoken


@pytest.mark.asyncio
async def test_c6_localizes_name_and_age_for_both_captions_and_tts_before_review(tmp_path, monkeypatch):
    mock_routes(monkeypatch)
    calls = []
    async def chat(*_args, **kwargs):
        calls.append(kwargs["stage"])
        assert "Hán–Việt" in kwargs["messages"][0]["content"]
        return json.dumps([{"id": "a", "text": "Ngô Tiểu Đồng 18 tuổi!",
                            "localized_terms": {"吴小彤": "Ngô Tiểu Đồng"}}])
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", chat)
    profile = LocalizationProfile(id="p", name="P")
    translated = await svc.translate_transcript(tx(), target_language="Vietnamese", profile=profile,
        user_settings=None, for_dub=True, checkpoint_dir=tmp_path / "translation")
    assert translated.metadata["localized_terms"] == {"吴小彤": "Ngô Tiểu Đồng"}
    spoken = mock_audio(monkeypatch)
    result = await svc.synthesize_timed_dub(translated, source_media=tmp_path / "source.mp4",
        target_language="Vietnamese", profile=profile, user_settings=None, work_dir=tmp_path / "dub")
    assert calls == ["translation"]
    assert spoken == ["Ngô Tiểu Đồng 18 tuổi!"]
    assert svc.caption_segments_from_localization(result.transcript)[0]["text"] == spoken[0]
    assert translated.segments[0].translated_text == spoken[0]
    assert result.rewrite_count == 0
    assert not (tmp_path / "dub/speech_forms.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(TTSProvider))
async def test_legacy_unlocalized_vietnamese_fails_before_tts_or_ai_for_all_providers(tmp_path, monkeypatch, provider):
    async def forbidden(*_a, **_kw):
        pytest.fail("C7 must not repair approved names through hidden AI")
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", forbidden)
    transcript = tx("吴小彤 18 tuổi!")
    transcript.metadata["target_language"] = "Vietnamese"
    with pytest.raises(ValueError, match="rerun translation"):
        await svc._prepare_dubbing_speech(transcript, profile=LocalizationProfile(id="p", name="P"),
            runtime=runtime(provider, "opaque-id"), ai=object(), work_dir=tmp_path)
    assert transcript.segments[0].translated_text == "吴小彤 18 tuổi!"


@pytest.mark.asyncio
async def test_only_explicit_literal_exception_keeps_han_and_other_target_languages_are_unchanged(tmp_path):
    profile = LocalizationProfile(id="p", name="P", protected_terms=["吴小彤"])
    transcript = tx("吴小彤 18 tuổi!")
    transcript.metadata["target_language"] = "vi"
    assert await svc._prepare_dubbing_speech(transcript, profile=profile, runtime=runtime()) == []
    transcript.metadata["target_language"] = "zh"
    assert await svc._prepare_dubbing_speech(transcript, profile=LocalizationProfile(id="p", name="P"), runtime=runtime()) == []


@pytest.mark.asyncio
async def test_dictionary_and_semantics_survive_checkpoint_and_corrupt_cache_is_retranslated(tmp_path, monkeypatch):
    mock_routes(monkeypatch)
    count = 0
    async def chat(*_args, **_kwargs):
        nonlocal count
        count += 1
        return '[{"id":"a","text":"Ngô Tiểu Đồng 18 tuổi!","localized_terms":{"吴小彤":"Ngô Tiểu Đồng"}}]'
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", chat)
    args = dict(target_language="Vietnamese", profile=LocalizationProfile(id="p", name="P"), user_settings=None,
                for_dub=True, checkpoint_dir=tmp_path)
    await svc.translate_transcript(tx(), **args)
    resumed = await svc.translate_transcript(tx(), **args)
    assert count == 1 and resumed.metadata["localized_terms"] == {"吴小彤": "Ngô Tiểu Đồng"}
    checkpoint = tmp_path / "checkpoint.json"
    cache = json.loads(checkpoint.read_text())
    cache["batches"]["0"]["items"][0]["text"] = "Ngô Tiểu Đồng 81 tuổi!"
    checkpoint.write_text(json.dumps(cache))
    assert (await svc.translate_transcript(tx(), **args)).segments[0].translated_text == "Ngô Tiểu Đồng 18 tuổi!"
    assert count == 2
    cache = json.loads(checkpoint.read_text())
    cache["batches"]["0"]["items"] = []
    checkpoint.write_text(json.dumps(cache))
    await svc.translate_transcript(tx(), **args)
    assert count == 3


@pytest.mark.asyncio
async def test_names_are_consistent_across_batches_and_resume_rejects_changed_timing(tmp_path, monkeypatch):
    mock_routes(monkeypatch)
    monkeypatch.setattr(svc, "_TRANSLATION_BATCH_SIZE", 1)
    transcript = tx()
    transcript.segments.append(transcript.segments[0].model_copy(update={"id": "b", "source_start_ms": 3000, "source_end_ms": 6000}))
    prompts = []
    async def chat(*_args, **kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        seg_id = "b" if '"id": "b"' in prompts[-1] else "a"
        return json.dumps([{"id": seg_id, "text": "Ngô Tiểu Đồng 18 tuổi!", "localized_terms": {"吴小彤": "Ngô Tiểu Đồng"}}])
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", chat)
    args = dict(target_language="Vietnamese", profile=LocalizationProfile(id="p", name="P"), user_settings=None,
                for_dub=True, checkpoint_dir=tmp_path)
    await svc.translate_transcript(transcript, **args)
    assert '"吴小彤": "Ngô Tiểu Đồng"' in prompts[1]
    transcript.segments[0].source_end_ms = 2900
    await svc.translate_transcript(transcript, **args)
    assert len(prompts) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", ["吴小彤 18 tuổi!", "Ngô Tiểu Đồng 81 tuổi!", "Ngô Tiểu Đồng 18 tuổi."])
async def test_c6_rejects_unlocalized_name_changed_age_and_flattened_exclamation(monkeypatch, candidate):
    async def chat(*_args, **_kwargs): return json.dumps([{"id": "a", "text": candidate}])
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", chat)
    _, failed = await svc._translate_batch_resilient(object(), tx().segments, target_language="Vietnamese",
        source_language="zh", context="", profile=LocalizationProfile(id="p", name="P"), for_dub=True)
    assert failed == {0}


@pytest.mark.asyncio
async def test_c7_cannot_remove_localized_name_or_flatten_source_intent(monkeypatch):
    from backend.services.dubbing_planner import DubbingCue, DubbingConfig, build_plan
    for candidate in ["Xin chào!", "Ngô Tiểu Đồng.", "吴小彤!"]:
        plan, chunks = build_plan([DubbingCue(1, "a", 0, 1000, "Xin chào Ngô Tiểu Đồng!")], DubbingConfig())
        async def chat(*_args, **_kwargs): return json.dumps([{"id": "a", "text": candidate}])
        monkeypatch.setattr(svc, "_call_ai_chat_resilient", chat)
        changed = await svc._adapt_chunk_items(object(), plan=plan, chunk=chunks[0], segment_by_id={"a": tx().segments[0]},
            target_language="Vietnamese", target_ms=1000,
            profile=LocalizationProfile(id="p", name="P", protected_terms=["Ngô Tiểu Đồng"]),
            issues=[], state={"measured_duration_ms": 5000})
        assert not changed and plan[0].spoken_text == "Xin chào Ngô Tiểu Đồng!"


@pytest.mark.asyncio
async def test_tts_wait_does_not_exhaust_unspent_rewrite_budget(tmp_path, monkeypatch):
    mock_routes(monkeypatch)
    mock_audio(monkeypatch)
    generated = 0
    async def generate(text, *, output_path, **_kwargs):
        nonlocal generated
        generated += 1
        await asyncio.sleep(.04)
        duration = 5000 if generated == 1 else 1000
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
            handle.writeframes(b"\x01\x00" * (duration * 16))
        return duration
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    seen = []
    async def adapt(*_args, **kwargs):
        seen.append(kwargs["deadline_at"] - asyncio.get_running_loop().time())
        for item in kwargs["plan"]: item.spoken_text = "Xin chào"
        return True
    monkeypatch.setattr(svc, "_adapt_chunk_items", adapt)
    profile = LocalizationProfile(id="p", name="P", dub_rewrite_attempts=1, video_retime_mode="off").model_copy(update={"rewrite_total_timeout_s": .02})
    await svc.synthesize_timed_dub(tx("Xin chào mọi người", "你好"), source_media=tmp_path / "source.mp4",
        target_language="Vietnamese", profile=profile, user_settings=None, work_dir=tmp_path / "dub")
    assert len(seen) == 1 and seen[0] > 0
