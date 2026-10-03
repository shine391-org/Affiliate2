# SPDX-License-Identifier: AGPL-3.0-only
"""Padded ASR cue windows must not erase real speech-onset pauses."""
import asyncio
import wave
from pathlib import Path

import pytest

from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider


@pytest.mark.parametrize("overrides,words,expected", [
    ({}, [{"word": "hello", "start": 2.14, "end": 2.30}], 2140),
    ({}, [], 1080),
    ({}, [{"word": "hello", "start": "bad", "end": 2.3}], 1080),
    ({}, [{"word": "hello", "start": float("nan"), "end": 2.3}], 1080),
    ({}, [{"word": "hello", "start": 3.0, "end": 3.1}], 1080),
    ({}, [{"word": "hello", "start": 0.5, "end": 0.7}], 1080),
    ({}, [{"word": "hello", "start": 2.3598, "end": 2.3599}], 1080),
    ({"target_start_ms": 1200}, [{"word": "hello", "start": 2.14, "end": 2.30}], 1200),
    ({"target_end_ms": 2300}, [{"word": "hello", "start": 2.14, "end": 2.30}], 1080),
])
def test_real_speech_anchor_preserves_fallback_and_expert_locks(tmp_path, monkeypatch, overrides, words, expected):
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id="s1", source_text="hello", translated_text="hello", translation_status="success",
        source_start_ms=1080, source_end_ms=2360, source_words=words, overrides=overrides,
    )])
    seen = []
    original = svc.build_plan
    def capture(cues, *args, **kwargs):
        seen.extend(cues)
        return original(cues, *args, **kwargs)
    monkeypatch.setattr(svc, "build_plan", capture)
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *_: (_ for _ in ()).throw(RuntimeError("no AI")))
    monkeypatch.setattr(svc, "_tts_config", lambda *_: svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS, default_voice="vi-VN-HoaiMyNeural", api_key="", model=None, base_url=None, region=None,
    ))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 4.0)
    async def generate(text, *, output_path, **kwargs):
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * 3200)
        return 200
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    result = asyncio.run(svc.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=LocalizationProfile(id="qa", name="QA", dub_rewrite_attempts=0, video_retime_mode="off"),
        user_settings=object(), work_dir=tmp_path / "work",
    ))
    assert seen[0].start_ms == expected
    assert seen[0].end_ms == overrides.get("target_end_ms", 2360)
    assert result.plan is not None
    assert result.plan[0].target_start_ms == expected
    assert tx.segments[0].source_start_ms == 1080  # Never mutate source provenance.


def test_padded_dialogue_is_not_one_front_loaded_synthesis(tmp_path, monkeypatch):
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id=f"s{i}", source_text=text, translated_text=text, translation_status="success",
        source_start_ms=start, source_end_ms=end,
        source_words=[{"word": text, "start": onset / 1000, "end": (end - 40) / 1000}],
    ) for i, (start, end, onset, text) in enumerate([
        (0, 1080, 180, "come here"), (1080, 2360, 2140, "yes"),
        (2360, 4520, 3740, "hello"), (4520, 6040, 4940, "my introduction"),
    ])])
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *_: (_ for _ in ()).throw(RuntimeError("no AI")))
    monkeypatch.setattr(svc, "_tts_config", lambda *_: svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS, default_voice="vi-VN-HoaiMyNeural", api_key="", model=None, base_url=None, region=None,
    ))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 7.0)
    texts = []
    async def generate(text, *, output_path, **kwargs):
        texts.append(text)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * 3200)
        return 200
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    result = asyncio.run(svc.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=LocalizationProfile(id="qa", name="QA", dub_rewrite_attempts=0, video_retime_mode="off"),
        user_settings=object(), work_dir=tmp_path / "work",
    ))
    assert texts == ["come here", "yes", "hello", "my introduction"]
    assert result.plan is not None
    assert [p.target_start_ms for p in result.plan] == [180, 2140, 3740, 4940]


@pytest.mark.parametrize("words", [
    [{"word": "hello", "start": 2.14, "end": 2.3}, {"word": "there", "start": 2.3, "end": float("inf")}],
    [{"word": "hello", "start": 2.14, "end": 2.3}, {"word": "there", "start": 9.0, "end": 9.1}],
    [{"word": "", "start": 2.14, "end": 2.3}],
])
def test_partial_or_invalid_word_alignment_does_not_move_onset(words):
    seg = LocalizationSegment(id="bad", source_text="hello there", source_start_ms=1080,
                              source_end_ms=2360, source_words=words)
    assert svc._speech_onset_ms(seg) == 1080


def test_overlapping_turn_words_anchor_to_earliest_time_not_list_order():
    seg = LocalizationSegment(id="overlap", source_text="hello there", source_start_ms=1080,
        source_end_ms=2360, source_words=[
            {"word": "hello", "start": 2.14, "end": 2.3},
            {"word": "there", "start": 1.5, "end": 1.8},
        ])
    assert svc._speech_onset_ms(seg) == 1500


def test_word_tail_crossing_cue_does_not_erase_valid_speech_onset():
    seg = LocalizationSegment(id="overlap-tail", source_text="哎呦吴小彤", source_start_ms=21720,
        source_end_ms=23300, source_words=[
            {"word": "哎", "start": 22.52, "end": 22.7},
            {"word": "吴", "start": 22.8, "end": 23.7},
            {"word": "彤", "start": 23.06, "end": 23.2},
        ])
    assert svc._speech_onset_ms(seg) == 22520


def test_short_leading_cue_does_not_block_splitting_viable_neighbours():
    """A 640 ms cue must not veto splitting the rest of a drifting chunk."""
    cues = [svc.DubbingCue(index=1, segment_id="lead", start_ms=0, end_ms=640,
                           text="Wow, hay", source_text="哇"),
            svc.DubbingCue(index=2, segment_id="a", start_ms=640, end_ms=2140,
                           text="Biết không, tôi không ở giữa", source_text="x"),
            svc.DubbingCue(index=3, segment_id="b", start_ms=2140, end_ms=3580,
                           text="Đây là bố cục hai phần ba", source_text="y")]
    plan, chunks = svc.build_plan(cues, svc.DubbingConfig(), split_before_segment_ids={"a", "b"})
    assert [c.item_indexes for c in chunks] == [[0], [1], [2]]
    assert plan is not None


def test_front_loaded_merged_audio_is_reviewed_not_called_aligned(tmp_path, monkeypatch):
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id=f"s{i}", source_text="same thought", translated_text="same thought",
        translation_status="success", source_start_ms=i * 3000, source_end_ms=(i + 1) * 3000,
    ) for i in range(2)])
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *_: (_ for _ in ()).throw(RuntimeError("no AI")))
    monkeypatch.setattr(svc, "_tts_config", lambda *_: svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS, default_voice="vi-VN-HoaiMyNeural", api_key="", model=None, base_url=None, region=None,
    ))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 7.0)
    async def generate(text, *, output_path, **kwargs):
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * 6400)
        return 400
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    result = asyncio.run(svc.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=LocalizationProfile(id="qa", name="QA", dub_rewrite_attempts=0, video_retime_mode="off"),
        user_settings=object(), work_dir=tmp_path / "work",
    ))
    drift = [i for i in result.issues if i["reason"] == "estimated_onset_drift"]
    assert len(drift) == 1
    assert drift[0]["segment_ids"] == ["s1"]
    assert drift[0]["drift_ms"] == -2800
    assert drift[0]["timing_basis"] == "estimated_chunk_weights"
    assert result.transcript.segments[1].qc_status == "review"


@pytest.mark.parametrize('repair_fails', [False, True])
def test_measured_drift_repairs_once_and_persists_layout_for_resume(tmp_path, monkeypatch, repair_fails):
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id=f"s{i}", source_text="same thought", translated_text="same thought",
        translation_status="success", source_start_ms=i * 3000, source_end_ms=(i + 1) * 3000,
        source_words=[{"word":"same thought", "start":i*3.0, "end":(i+1)*3.0}],
    ) for i in range(2)])
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *_: (_ for _ in ()).throw(RuntimeError("no AI")))
    monkeypatch.setattr(svc, "_tts_config", lambda *_: svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS, default_voice="vi-VN-HoaiMyNeural", api_key="", model=None, base_url=None, region=None,
    ))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 7.0)
    calls=[]
    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        if repair_fails and len(calls) > 1:
            raise TimeoutError('provider unavailable during speculative split')
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * 6400)
        return 400
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    async def run(**kwargs):
        return await svc.synthesize_timed_dub(
            tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
            profile=LocalizationProfile(id="qa", name="QA", dub_rewrite_attempts=0, video_retime_mode="off"),
            user_settings=object(), work_dir=tmp_path / "work", reuse_existing_chunks=True, **kwargs,
        )
    first=asyncio.run(run())
    if repair_fails:
        assert first.chunk_count == 1
        assert first.dub_track.is_file()
        assert any(i['reason'] == 'cadence_repair_unavailable' for i in first.issues)
        assert any(i['reason'] == 'estimated_onset_drift' for i in first.issues)
        assert not (tmp_path / 'work' / 'cadence_layout.json').exists()
        assert not list((tmp_path / 'work').glob('cadence-*'))
        return
    assert first.chunk_count==2
    assert first.plan is not None and first.chunks is not None
    assert [p.target_start_ms for p in first.plan]==[0,3000]
    assert not [i for i in first.issues if i['reason']=='estimated_onset_drift']
    count=len(calls)
    second=asyncio.run(run())
    assert len(calls)==count
    assert second.chunks is not None
    assert [c.item_indexes for c in first.chunks]==[c.item_indexes for c in second.chunks]
    third=asyncio.run(run(regenerate_chunk_ids={2}))
    assert len(calls)==count+1
    assert third.chunks is not None
    assert [c.item_indexes for c in third.chunks]==[c.item_indexes for c in second.chunks]
    tx.segments[1].overrides={'target_start_ms':3100}
    with pytest.raises(RuntimeError,match='layout changed'):
        asyncio.run(run(regenerate_chunk_ids={2}))
    assert len(calls)==count+1


def test_repair_that_creates_overflow_is_rejected_and_original_layout_kept(tmp_path, monkeypatch):
    """Splitting must not trade a cadence warning for a timing overflow."""
    texts = ["Hi", "This is a much much longer sentence that simply cannot fit"]
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id=f"s{i}", source_text="same thought", translated_text=texts[i],
        translation_status="success", source_start_ms=i * 3000, source_end_ms=(i + 1) * 3000,
        source_words=[{"word":"same thought", "start":i*3.0, "end":(i+1)*3.0}],
    ) for i in range(2)])
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *_: (_ for _ in ()).throw(RuntimeError("no AI")))
    monkeypatch.setattr(svc, "_tts_config", lambda *_: svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS, default_voice="vi-VN-HoaiMyNeural", api_key="", model=None, base_url=None, region=None,
    ))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 7.0)
    calls=[]
    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        duration = 6400 if len(calls) == 1 or "cannot fit" in text else 800
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * int(duration * 16))
        return duration
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    result = asyncio.run(svc.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=LocalizationProfile(id="qa", name="QA", dub_rewrite_attempts=0, video_retime_mode="off"),
        user_settings=object(), work_dir=tmp_path / "work", reuse_existing_chunks=True,
    ))
    assert result.chunk_count == 1
    assert result.dub_track.is_file()
    assert not (tmp_path / "work" / "cadence_layout.json").exists()
    reasons = {issue["reason"] for issue in result.issues}
    assert "estimated_onset_drift" in reasons
    assert "cadence_repair_rejected_overflow" in reasons
    assert not any(issue.get("reason") == "timing_overflow" for issue in result.issues)
