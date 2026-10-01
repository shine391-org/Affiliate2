# SPDX-License-Identifier: AGPL-3.0-only
"""Rhythm contracts: allocate silence before considering text rescue."""
import pytest

from backend.services import dubbing_planner as dp


def cue(i, start, end, text, **kw):
    return dp.DubbingCue(i, f"s{i}", start, end, text, **kw)


@pytest.mark.parametrize("source", ["", "First sentence。"])
def test_short_complete_sentences_that_fit_stay_separate(source):
    _, chunks = dp.build_plan([
        cue(1, 0, 1200, "Câu đầu." if not source else "Câu đầu", source_text=source),
        cue(2, 1400, 2600, "Câu sau."),
    ], dp.DubbingConfig())
    assert [c.item_indexes for c in chunks] == [[0], [1]]


def test_real_orphan_prefers_following_silence_and_retains_onset():
    plan, chunks = dp.build_plan([
        cue(1, 245, 1019, "Cắt phần môi xuống cho tôi"),
        cue(2, 1905, 1926, "Ừ"),
        cue(3, 3868, 4420, "Câu sau."),
    ], dp.DubbingConfig())
    assert [c.item_indexes for c in chunks] == [[0], [1], [2]]
    assert chunks[1].start_ms == 1905
    assert chunks[1].available_ms >= plan[1].estimated_duration_ms / 1.15
    assert chunks[1].start_ms - chunks[0].end_ms >= 350
    assert chunks[1].end_ms < chunks[2].start_ms


def test_terminal_orphan_can_use_real_trailing_silence():
    _, chunks = dp.build_plan([
        cue(1, 0, 1000, "First."), cue(2, 1905, 1926, "Ừ"),
    ], dp.DubbingConfig(), timeline_end_ms=3000)
    assert len(chunks) == 2
    assert chunks[1].start_ms == 1905
    assert chunks[1].end_ms > 1926
    assert chunks[1].end_ms <= 3000


def test_terminal_orphan_without_trailing_silence_borrows_before_but_keeps_pause():
    _, chunks = dp.build_plan([
        cue(1, 0, 1000, "First."), cue(2, 1905, 1926, "Ừ"),
    ], dp.DubbingConfig())
    assert len(chunks) == 2
    assert chunks[1].start_ms < 1905
    assert chunks[1].start_ms - chunks[0].end_ms >= 350
    assert chunks[1].end_ms == 1926


def test_measured_allocation_is_bounded_idempotent_and_preserves_pause():
    cfg = dp.DubbingConfig()
    plan, chunks = dp.build_plan([
        cue(1, 0, 1000, "First."), cue(2, 1900, 2900, "Second."),
    ], cfg)
    chunks[0].actual_duration_ms = 1600
    dp.allocate_silence(plan, chunks, cfg)
    first = [(c.start_ms, c.end_ms) for c in chunks]
    dp.allocate_silence(plan, chunks, cfg)
    assert first == [(c.start_ms, c.end_ms) for c in chunks]
    assert chunks[1].start_ms - chunks[0].end_ms >= 350
    assert chunks[0].available_ms > 1000


def test_expert_timing_lock_is_not_merged_or_expanded():
    _, chunks = dp.build_plan([
        cue(1, 0, 1000, "first"),
        cue(2, 1200, 1221, "Ừ", timing_locked=True),
        cue(3, 2500, 3500, "third"),
    ], dp.DubbingConfig())
    assert [c.item_indexes for c in chunks] == [[0], [1], [2]]
    assert (chunks[1].start_ms, chunks[1].end_ms) == (1200, 1221)


def test_impossible_cue_with_no_budget_does_not_erase_sentence_boundary():
    _, chunks = dp.build_plan([
        cue(1, 0, 1000, "First."),
        cue(2, 1000, 1021, "Ừ"),
        cue(3, 1021, 2021, "third", speaker_id="other"),
    ], dp.DubbingConfig())
    assert [c.item_indexes for c in chunks] == [[0], [1], [2]]
    assert chunks[1].available_ms == 21


@pytest.mark.parametrize("gap", [351, 886, 1500, 1501])
def test_real_pause_is_never_removed_by_text_rescue(gap):
    _, chunks = dp.build_plan([
        cue(1, 0, 1000, "first"),
        cue(2, 1000 + gap, 1021 + gap, "Ừ"),
    ], dp.DubbingConfig())
    assert len(chunks) == 2
    assert chunks[1].start_ms - chunks[0].end_ms >= min(gap, 350)


def test_allocator_never_extends_ordinary_chunk_beyond_eight_seconds():
    plan, chunks = dp.build_plan([
        cue(1, 0, 7900, "First."), cue(2, 9900, 11000, "Second."),
    ], dp.DubbingConfig())
    chunks[0].actual_duration_ms = 15000
    dp.allocate_silence(plan, chunks, dp.DubbingConfig())
    assert chunks[0].available_ms == 8000


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("overestimated_neighbour", [False, True])
async def test_pipeline_preserves_pause_with_measured_audio(
    tmp_path, monkeypatch, locked, overestimated_neighbour,
):
    import array
    import math
    import wave
    from backend.core.localization import (
        LocalizationProfile, LocalizationSegment, LocalizationTranscript,
    )
    from backend.services import localization_service as svc
    from backend.services.tts_service import TTSProvider

    second_text = "one two three four five six" if overestimated_neighbour else "Ừ"
    durations = {"First.": 1700 if overestimated_neighbour else 1500,
                 second_text: 100 if overestimated_neighbour else 400, "Last.": 400}
    if overestimated_neighbour and not locked:
        monkeypatch.setattr(svc, "_profile_ai_client", lambda *_: object())
        async def forbidden_rewrite(*args, **kwargs):
            pytest.fail("An estimated neighbour must not force a measured rewrite")
        monkeypatch.setattr(svc, "_adapt_chunk_items", forbidden_rewrite)
    texts = []

    async def generate(text, *, output_path, **kwargs):
        texts.append(text)
        rate = 16000
        samples = array.array("h", (
            round(5000 * math.sin(2 * math.pi * 440 * n / rate))
            for n in range(rate * durations[text] // 1000)
        ))
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(samples.tobytes())
        return durations[text]

    runtime = svc.TTSRuntimeConfig(
        provider=TTSProvider.OPENAI_COMPATIBLE, default_voice="qa", api_key="",
        model=None, base_url=None, region=None,
    )
    monkeypatch.setattr(svc, "_tts_config", lambda *_: runtime)
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_: 4.0)
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id=f"s{i}", source_text=text, translated_text=text,
            source_start_ms=start, source_end_ms=end, translation_status="success",
            overrides={"target_start_ms": start, "target_end_ms": end} if locked else {},
        )
        for i, (start, end, text) in enumerate([
            (0, 1000, "First."), (1905, 1926, second_text), (3000, 4000, "Last."),
        ])
    ])
    profile = LocalizationProfile(
        id="qa", name="QA", video_retime_mode="off",
        dub_rewrite_attempts=2 if overestimated_neighbour and not locked else 0,
    )
    if locked:
        with pytest.raises(svc.LocalizationTimingError):
            await svc.synthesize_timed_dub(
                tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
                profile=profile, user_settings=object(), work_dir=tmp_path / "work",
            )
        return
    result = await svc.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=profile, user_settings=object(), work_dir=tmp_path / "work",
    )
    assert result.chunks is not None
    assert texts == ["First.", second_text, "Last."]
    assert result.chunks[1].start_ms == 1905
    assert result.chunks[1].available_ms == durations[second_text]
    assert result.chunks[1].speed_factor == 1.0
    assert result.chunks[1].start_ms - result.chunks[0].end_ms >= 350
    assert not [issue for issue in result.issues if issue["reason"] == "timing_overflow"]
    # Real FFmpeg fitting/composition, not a stubbed timeline. Audio in the
    # retained pause must be digital silence; the micro-cue must be audible.
    with wave.open(str(result.dub_track), "rb") as wav:
        rate = wav.getframerate()
        wav.setpos(round(1.600 * rate))
        pause = array.array("h", wav.readframes(round(0.250 * rate)))
        wav.setpos(round(1.930 * rate))
        speech = array.array("h", wav.readframes(round(0.150 * rate)))
    assert max(map(abs, pause)) == 0
    assert max(map(abs, speech)) > 1000


def test_randomized_allocation_preserves_boundaries_and_retry_invariants():
    import random
    rng = random.Random(391)
    for _ in range(400):
        cfg = dp.DubbingConfig(
            max_chunk_size=rng.choice([1, 2, 5]),
            gap_tolerance_ms=rng.choice([0, 100, 350, 800, 1500]),
        )
        cues = []
        end = 0
        for i in range(rng.randint(2, 18)):
            start = end + rng.choice([0, 63, 200, 350, 351, 886, 1501])
            end = start + rng.choice([1, 21, 200, 1000, 7900, 8001])
            cues.append(cue(
                i, start, end, rng.choice(["Ừ", "one two", "Done."]),
                speaker_id=rng.choice([None, "a", "b"]),
                voice_id=rng.choice([None, "v1", "v2"]),
                timing_locked=rng.randrange(7) == 0,
            ))
        plan, chunks = dp.build_plan(cues, cfg, timeline_end_ms=end + 2000)
        assert sorted(i for c in chunks for i in c.item_indexes) == list(range(len(cues)))
        for chunk in chunks:
            indexes = chunk.item_indexes
            assert len({(cues[i].speaker_id, cues[i].voice_id) for i in indexes}) == 1
            for i in indexes[1:]:
                assert cues[i].start_ms - cues[i - 1].end_ms <= 350
                assert not dp._STRONG_END.search(cues[i - 1].text)
                assert not cues[i - 1].timing_locked and not cues[i].timing_locked
            if any(cues[i].timing_locked for i in indexes):
                assert len(indexes) == 1
                assert (chunk.start_ms, chunk.end_ms) == (cues[indexes[0]].start_ms, cues[indexes[0]].end_ms)
            chunk.actual_duration_ms = rng.randint(180, 16000)
        dp.allocate_silence(plan, chunks, cfg, timeline_end_ms=end + 2000)
        first = [(c.start_ms, c.end_ms) for c in chunks]
        dp.allocate_silence(plan, chunks, cfg, timeline_end_ms=end + 2000)
        assert first == [(c.start_ms, c.end_ms) for c in chunks]
        for pos, chunk in enumerate(chunks):
            start = min(cues[i].start_ms for i in chunk.item_indexes)
            stop = max(cues[i].end_ms for i in chunk.item_indexes)
            assert 0 <= chunk.start_ms <= start
            assert stop <= chunk.end_ms <= end + 2000
            assert chunk.start_ms >= max(0, start - cfg.gap_tolerance_ms)
            assert chunk.end_ms <= stop + cfg.gap_tolerance_ms
            if pos:
                prev = chunks[pos - 1]
                source_gap = start - max(cues[i].end_ms for i in prev.item_indexes)
                assert chunk.start_ms - prev.end_ms >= min(source_gap, 350)


def test_upstream_oversized_cue_is_not_grown_by_allocator_or_rescue():
    plan, chunks = dp.build_plan([
        cue(1, 0, 9000, "first"), cue(2, 9000, 9021, "Ừ"),
    ], dp.DubbingConfig())
    assert [c.item_indexes for c in chunks] == [[0], [1]]
    chunks[0].actual_duration_ms = 20000
    dp.allocate_silence(plan, chunks, dp.DubbingConfig(), timeline_end_ms=11000)
    assert chunks[0].available_ms == 9000
    assert chunks[1].available_ms * 1.30 >= 278


def test_actual_duration_reclaims_estimated_leading_and_trailing_slack():
    plan, chunks = dp.build_plan([
        cue(1, 1000, 1100, "one two three four five six seven eight."),
        cue(2, 3500, 4500, "Done."),
    ], dp.DubbingConfig())
    assert chunks[0].start_ms < 1000 or chunks[0].end_ms > 1100
    chunks[0].actual_duration_ms = 80
    chunks[1].actual_duration_ms = 500
    dp.allocate_silence(plan, chunks, dp.DubbingConfig(), timeline_end_ms=5000)
    assert (chunks[0].start_ms, chunks[0].end_ms) == (1000, 1100)
    assert (chunks[1].start_ms, chunks[1].end_ms) == (3500, 4500)


def test_measured_orphan_uses_natural_speed_when_trailing_slack_is_available():
    plan, chunks = dp.build_plan([
        cue(1, 245, 1019, "Cắt phần môi xuống cho tôi"),
        cue(2, 1905, 1926, "Ừ"),
        cue(3, 3868, 4420, "Chào chị"),
    ], dp.DubbingConfig())
    for chunk, actual in zip(chunks, [1589, 338, 674]):
        chunk.actual_duration_ms = actual
    dp.allocate_silence(plan, chunks, dp.DubbingConfig(), timeline_end_ms=70424)
    assert (chunks[0].start_ms, chunks[0].end_ms) == (173, 1555)
    assert (chunks[1].start_ms, chunks[1].end_ms) == (1905, 2243)
    assert (chunks[2].start_ms, chunks[2].end_ms) == (3868, 4542)
    _, fitted, _ = dp.fit_timeline(plan, chunks, dp.DubbingConfig())
    assert fitted[1].speed_factor == 1.0
    assert fitted[2].speed_factor == 1.0


def test_targeted_reallocation_never_resets_non_target_window():
    plan, chunks = dp.build_plan([
        cue(1, 0, 1000, "First."), cue(2, 1905, 1926, "Ừ"),
    ], dp.DubbingConfig(), timeline_end_ms=3000)
    chunks[0].start_ms, chunks[0].end_ms = 100, 1200
    chunks[1].actual_duration_ms = 338
    dp.allocate_silence(
        plan, chunks, dp.DubbingConfig(), timeline_end_ms=3000, only_chunk_ids={2},
    )
    assert (chunks[0].start_ms, chunks[0].end_ms) == (100, 1200)
    assert (chunks[1].start_ms, chunks[1].end_ms) == (1905, 2243)


def test_silence_budget_is_not_spent_when_speech_already_fits():
    _, chunks = dp.build_plan([
        cue(1, 0, 1200, "First."), cue(2, 2200, 3400, "Second."),
    ], dp.DubbingConfig())
    assert [(c.start_ms, c.end_ms) for c in chunks] == [(0, 1200), (2200, 3400)]
