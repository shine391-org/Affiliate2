# SPDX-License-Identifier: AGPL-3.0-only
"""D1 — earliest invalid artifact must invalidate its whole dependency closure.

The manifest records each stage as ``complete`` plus the artifacts that prove
it. When one of those artifacts is lost (deleted delivery file, wiped mixed
audio, corrupt render candidate) the stage is rebuilt, but every LATER stage
still reads ``status == "complete"`` and is reused as-is. The result is a run
whose QC report, approval surface and delivered video describe artifacts that
no longer exist.

These tests drive the real ``LocalizationRun`` and the real
``run_tool_translate`` runner. Leaves (ASR/semantic/translation/TTS/mix/render/
QC) are doubles; no LIVE provider is claimed.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import Settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job
from backend.services import localization_service as svc
from backend.services.localization_stage_service import (
    STAGES,
    LocalizationRun,
    compact_localization_workspace,
)

UID = "local"
SOURCE_BYTES = b"dependency-closure-source-media"


# ── schema / DB ────────────────────────────────────────────────────────────


async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    import asyncio

    asyncio.run(_create_schema())


@pytest.fixture(autouse=True)
def _clean_rows():
    import asyncio

    async def _wipe():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            for row in (await db.execute(select(Job))).scalars().all():
                await db.delete(row)
            await db.commit()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: root))
    return root


# ── fixtures ───────────────────────────────────────────────────────────────


def _transcript(tag: str = "Xin chào") -> LocalizationTranscript:
    return LocalizationTranscript(
        text=tag,
        language="en",
        segments=[
            LocalizationSegment(
                id=f"s{i}", source_text=tag, translated_text=tag,
                source_start_ms=i * 3000, source_end_ms=i * 3000 + 1000,
                translation_status="success", qc_status="pass",
                overrides={"target_start_ms": i * 3000,
                           "target_end_ms": i * 3000 + 1000},
            )
            for i in range(2)
        ],
    )


def _profile(**updates) -> LocalizationProfile:
    base = LocalizationProfile(
        id="qa-closure", name="QA Closure",
        audio_mode="fast_duck", target_aspect="source",
        video_retime_mode="off", dub_rewrite_attempts=0,
        tts_provider="edge_tts", tts_voice="vi-VN-HoaiMyNeural",
    )
    return base.model_copy(update=updates)


def _make_run(job_id: str, storage: Path, *, mode="full_dub"):
    source = storage / f"{job_id}-origin.mp4"
    source.write_bytes(SOURCE_BYTES)
    run = LocalizationRun(
        job_id, source_path=str(source),
        effective_profile=_profile().model_dump(),
        request={
            "target_language": "Vietnamese", "mode": mode, "voice_id": "",
            "caption_style": "viral", "emoji_style": "moderate",
            "profile_id": None, "review_translation": False,
        },
    )
    run.ensure_source_copy(source)
    return run


async def _seed_job(job_id: str):
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, user_id=UID, job_type="tool:translate",
                   status="running", title=job_id))
        await db.commit()


def _patch_leaves(monkeypatch, tmp_path, *, spies: dict):
    """Stub every leaf so only resume/closure decisions are exercised."""
    from backend.core import tool_runners as runner
    from backend.services import caption_service, localization_qc_service, video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible",
                            localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())

    monkeypatch.setattr(video_utils, "probe_stream_inventory",
                        lambda *a, **k: video_utils.MediaStreamInventory(
                            readable=True, has_video=True, has_audio=True,
                            width=1080, height=1920, duration=12.0))
    monkeypatch.setattr(video_utils, "probe_duration", lambda *a, **k: 12.0)

    async def transcribe(*a, **k):
        spies["asr"] = spies.get("asr", 0) + 1
        return _transcript("nguồn")

    async def semantic(transcript, **k):
        spies["semantic"] = spies.get("semantic", 0) + 1
        return transcript

    async def translate(transcript, **k):
        spies["translation"] = spies.get("translation", 0) + 1
        return transcript.model_copy(deep=True)

    monkeypatch.setattr(svc, "transcribe_for_localization", transcribe)
    monkeypatch.setattr(svc, "prepare_semantic_transcript", semantic)
    monkeypatch.setattr(svc, "translate_transcript", translate)

    async def synth(localized, **kwargs):
        spies["dubbing"] = spies.get("dubbing", 0) + 1
        work = Path(kwargs["work_dir"])
        work.mkdir(parents=True, exist_ok=True)
        track = work / "dub_track.wav"
        track.write_bytes(b"dub-track")
        from backend.services.localization_service import TimedDubResult

        return TimedDubResult(transcript=localized, dub_track=track,
                              block_paths=[], issues=[], chunk_count=2,
                              rewrite_count=0)

    monkeypatch.setattr(svc, "synthesize_timed_dub", synth)

    async def mix(source, audio, destination, *a, **k):
        spies["mix"] = spies.get("mix", 0) + 1
        Path(destination).write_bytes(b"mixed")

    monkeypatch.setattr(svc, "mix_dub_audio", mix)

    async def gen(segments, *, output_path, **k):
        Path(output_path).write_text("[Events]\n", encoding="utf-8")
        return output_path

    async def burn(video_path, ass_path, output_path, **k):
        spies["render"] = spies.get("render", 0) + 1
        Path(output_path).write_bytes(b"rendered")
        return output_path

    monkeypatch.setattr(caption_service, "generate_captions_ass", gen)
    monkeypatch.setattr(caption_service, "burn_captions", burn)

    def qc(**k):
        spies["qc"] = spies.get("qc", 0) + 1
        return SimpleNamespace(as_dict=lambda: {
            "status": "NEEDS_REVIEW", "failures": [], "warnings": []})

    monkeypatch.setattr(localization_qc_service, "evaluate_localization_qc", qc)
    monkeypatch.setattr("backend.api.tools.tool_out_path",
                        lambda job_id, suffix: tmp_path / f"out-{job_id}{suffix}")


async def _run(job_id, run):
    from backend.core.tool_runners import run_tool_translate

    await run_tool_translate(
        job_id, run.source_path, "Vietnamese", run.request["mode"],
        "", "viral", "none", cleanup_input=False,
    )


async def _complete_pipeline(job_id, run, monkeypatch, tmp_path, spies):
    """Drive one full successful pipeline through the stubbed leaves."""
    _patch_leaves(monkeypatch, tmp_path, spies=spies)
    await _run(job_id, run)
    fresh = LocalizationRun.load(job_id)
    broken = {
        stage: fresh.record(stage).get("error")
        for stage in STAGES if fresh.record(stage)["status"] != "complete"
    }
    assert not broken, f"pipeline did not complete: {broken}"
    spies.clear()
    return fresh


# ══════════════════════════════════════════════════════════════════════════
# Manifest-level: earliest damaged stage detection
# ══════════════════════════════════════════════════════════════════════════


def test_missing_artifact_marks_its_stage_damaged(storage):
    """A stage whose named artifact vanished is damaged even while LATER
    stages still claim completion -- that later claim is the stale reuse."""
    run = _make_run("manifest-damaged", storage)
    run.mark_complete_without_artifacts("separation")
    asr_path = run.save_json("asr", "transcript.json", _transcript().model_dump())
    run.complete("asr", artifacts={"transcript": asr_path})
    for stage in ("semantic", "translation"):
        path = run.save_json(stage, "transcript.json", _transcript().model_dump())
        run.complete(stage, artifacts={"transcript": path})

    assert run.earliest_damaged_stage() is None

    Path(asr_path).unlink()
    assert run.earliest_damaged_stage() == "asr"


def test_intact_manifest_reports_no_damage(storage):
    run = _make_run("manifest-intact", storage)
    run.mark_complete_without_artifacts("separation")
    transcript = run.save_json("asr", "transcript.json", _transcript().model_dump())
    run.complete("asr", artifacts={"transcript": transcript})

    assert run.earliest_damaged_stage() is None


def test_compaction_marks_reclaimed_stages_not_damaged(storage):
    """Retention compaction is a DELIBERATE reclaim, not artifact loss.

    A compacted workspace keeps translation/dubbing/mix/render/QC on purpose so
    a rerender stays cheap. Treating the reclaimed separation/asr/semantic
    stages as damage would nuke the retained last-good chain.
    """
    run = _make_run("manifest-compacted", storage)
    run.mark_complete_without_artifacts("separation")
    for stage in ("asr", "semantic", "translation"):
        path = run.save_json(stage, "transcript.json", _transcript().model_dump())
        run.complete(stage, artifacts={"transcript": path})

    assert compact_localization_workspace(run.job_id) >= 1
    fresh = LocalizationRun.load(run.job_id)
    assert fresh.artifact("translation", "transcript") is not None
    assert fresh.earliest_damaged_stage() is None


# ══════════════════════════════════════════════════════════════════════════
# Runner-level: closure rebuild, never stale reuse
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_lost_mixed_audio_rebuilds_render_and_qc(storage, monkeypatch, tmp_path):
    """The mix artifact is gone -> mix, render AND QC must all be recomputed."""
    run = _make_run("closure-mix", storage)
    await _seed_job(run.job_id)
    spies: dict = {}
    fresh = await _complete_pipeline(
        run.job_id, run, monkeypatch, tmp_path, spies)

    mixed = fresh.artifact("audio_mix", "mixed_audio")
    assert mixed is not None
    mixed.unlink()

    await _run(run.job_id, fresh)

    assert spies.get("mix") == 1, "lost mixed audio must be rebuilt"
    assert spies.get("render") == 1, "a rebuilt mix must invalidate the old render"
    assert spies.get("qc") == 1, "QC must not certify a render built on other audio"
    # Upstream is untouched and reused.
    assert "dubbing" not in spies
    assert "translation" not in spies
    assert spies.get("asr") is None


@pytest.mark.asyncio
async def test_lost_final_video_rebuilds_render_and_qc(storage, monkeypatch, tmp_path):
    """A lost render/delivery file must never leave the old QC report in place."""
    run = _make_run("closure-render", storage)
    await _seed_job(run.job_id)
    spies: dict = {}
    fresh = await _complete_pipeline(
        run.job_id, run, monkeypatch, tmp_path, spies)

    delivered = fresh.artifact("final_render", "final_video")
    assert delivered is not None
    delivered.unlink()

    await _run(run.job_id, fresh)

    assert spies.get("render") == 1, "a lost delivery must be re-rendered"
    assert spies.get("qc") == 1, "QC must be regenerated for the new render"
    assert "mix" not in spies, "audio mix is upstream of the lost render"
    assert "translation" not in spies


@pytest.mark.asyncio
async def test_lost_asr_transcript_rebuilds_translation_and_qc(
    storage, monkeypatch, tmp_path,
):
    """Losing C5 must not silently reuse a translation built from other words."""
    run = _make_run("closure-asr", storage)
    await _seed_job(run.job_id)
    spies: dict = {}
    fresh = await _complete_pipeline(
        run.job_id, run, monkeypatch, tmp_path, spies)

    fresh.artifact("asr", "transcript").unlink()
    await _run(run.job_id, fresh)

    assert spies.get("asr") == 1
    assert spies.get("semantic") == 1, "semantic cues descend from the ASR transcript"
    assert spies.get("translation") == 1, "a stale translation must not be reused"
    assert spies.get("dubbing") == 1
    assert spies.get("render") == 1
    assert spies.get("qc") == 1


@pytest.mark.asyncio
async def test_intact_resume_reuses_every_upstream_stage(storage, monkeypatch, tmp_path):
    """The gate for any closure work: a healthy resume must not recompute."""
    run = _make_run("closure-intact", storage)
    await _seed_job(run.job_id)
    spies: dict = {}
    fresh = await _complete_pipeline(
        run.job_id, run, monkeypatch, tmp_path, spies)

    before = {
        stage: (dict(fresh.record(stage)), fresh.artifact(stage, "transcript"))
        for stage in ("asr", "semantic", "translation")
    }

    await _run(run.job_id, fresh)

    assert spies == {}, "an intact manifest must reuse every stage"
    after = LocalizationRun.load(run.job_id)
    for stage, (record, artifact) in before.items():
        assert dict(after.record(stage)) == record
        if artifact is not None:
            assert after.artifact(stage, "transcript").read_bytes() == artifact.read_bytes()


@pytest.mark.asyncio
async def test_compacted_workspace_still_reuses_translation(
    storage, monkeypatch, tmp_path,
):
    """Retention compaction must not turn a cheap rerender into a full rebuild."""
    run = _make_run("closure-compact", storage)
    await _seed_job(run.job_id)
    spies: dict = {}
    await _complete_pipeline(run.job_id, run, monkeypatch, tmp_path, spies)

    assert compact_localization_workspace(run.job_id) >= 1
    compacted = LocalizationRun.load(run.job_id)
    assert compacted.artifact("translation", "transcript") is not None

    await _run(run.job_id, compacted)

    assert spies.get("translation") is None, "compaction must not discard accepted C6"
    assert spies.get("dubbing") is None
    assert spies.get("mix") is None
    assert json.loads(
        (compacted.artifact("qc", "report")).read_text(encoding="utf-8")
    )["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize('invalidate',[False,True])
def test_rebuilt_stage_no_longer_inherits_compaction_exemption(storage,invalidate):
    run=_make_run('compaction-rebuild',storage)
    for stage in ('asr','semantic','translation'):
        path=run.save_json(stage,'transcript.json',_transcript().model_dump())
        run.complete(stage,artifacts={'transcript':path})
    compact_localization_workspace(run.job_id)
    run=LocalizationRun.load(run.job_id)
    assert 'asr' in run.compacted_stages()
    if invalidate:
        run.invalidate_from('asr')
    rebuilt=run.save_json('asr','transcript.json',_transcript('rebuilt').model_dump())
    run.complete('asr',artifacts={'transcript':rebuilt})
    rebuilt.unlink()
    fresh=LocalizationRun.load(run.job_id)
    assert 'asr' not in fresh.compacted_stages()
    assert fresh.earliest_damaged_stage()=='asr'


@pytest.mark.asyncio
@pytest.mark.parametrize('previous_approval',[False,True])
async def test_guard_uncertainty_forces_existing_human_gate_before_tts(storage,monkeypatch,tmp_path,previous_approval):
    job_id='uncertain-translation-human-gate'
    run=_make_run(job_id,storage)
    run.update_request({**run.request,'translation_review_approved':previous_approval})
    await _seed_job(job_id)
    spies={}
    _patch_leaves(monkeypatch,tmp_path,spies=spies)
    async def uncertain(transcript,**kwargs):
        out=transcript.model_copy(deep=True)
        out.metadata['translation_guard_review']={'s0':[{'kind':'article_asymmetry','severity':'uncertain','action':'review'}]}
        out.segments[0].qc_status='review'
        return out
    monkeypatch.setattr(svc,'translate_transcript',uncertain)
    await _run(job_id,run)
    fresh=LocalizationRun.load(job_id)
    async with AsyncSessionLocal() as db:
        job=await db.get(Job,job_id)
        assert job.status=='awaiting_review'
    assert fresh.request['review_translation'] is True
    assert fresh.request['translation_review_pending'] is True
    assert fresh.request['translation_review_approved'] is False
    assert fresh.record('translation')['status']=='complete'
    assert fresh.artifact('translation','transcript').is_file()
    assert spies.get('dubbing',0)==0
    assert spies.get('render',0)==0


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit_review',[False,True])
@pytest.mark.parametrize('finding',['uncertain','draft'])
async def test_auto_continuity_preserves_explicit_gate_and_passes_actual_draft_to_consumers(
    storage,monkeypatch,tmp_path,explicit_review,finding,
):
    """Real runner/SQLite/manifest, media/provider leaves are MOCKED, not live E2E."""
    from backend.services import caption_service
    job_id=f'auto-continuity-{finding}-{explicit_review}'
    run=_make_run(job_id,storage)
    run.update_effective_profile(_profile(translation_automation='auto').model_dump())
    run.update_request({**run.request,'review_translation':explicit_review,'translation_review_approved':True})
    await _seed_job(job_id)
    spies={}
    _patch_leaves(monkeypatch,tmp_path,spies=spies)
    seen={}
    async def translate(transcript,**kwargs):
        seen['source_media']=kwargs.get('source_media')
        out=transcript.model_copy(deep=True)
        s=out.segments[0]
        s.source_text='三公斤';s.translated_text='4 kg.' if finding=='draft' else '3 kg.'
        s.translation_status='draft' if finding=='draft' else 'success'
        s.qc_status='review'
        issues=[{'kind':'quantity_lost' if finding=='draft' else 'article_asymmetry',
                 'severity':'critical' if finding=='draft' else 'uncertain','action':'review'}]
        out.metadata['translation_guard_review']={s.id:issues}
        if finding=='draft':
            out.metadata['translation_auto_drafts']={s.id:{'source_text':s.source_text,
                'target_text':s.translated_text,'issues':issues,'attempts':[{'accepted':False,'attempt':1}]}}
        return out
    monkeypatch.setattr(svc,'translate_transcript',translate)
    original_synth=svc.synthesize_timed_dub
    async def synth(localized,**kwargs):
        seen['spoken']=localized.segments[0].translated_text
        seen['status']=localized.segments[0].translation_status
        return await original_synth(localized,**kwargs)
    monkeypatch.setattr(svc,'synthesize_timed_dub',synth)
    original_gen=caption_service.generate_captions_ass
    async def captions(segments,**kwargs):
        seen['caption_texts']=[s.get('text') for s in segments]
        return await original_gen(segments,**kwargs)
    monkeypatch.setattr(caption_service,'generate_captions_ass',captions)
    await _run(job_id,run)
    fresh=LocalizationRun.load(job_id)
    assert fresh.request['review_translation'] is explicit_review
    assert fresh.request['translation_review_approved'] is False, 'C6 rebuild must reset old approval'
    assert fresh.record('translation')['status']=='complete'
    assert seen['source_media']==fresh.source_path, 'Targeted recheck must receive immutable source'
    async with AsyncSessionLocal() as db:
        job=await db.get(Job,job_id)
        if explicit_review:
            assert job.status=='awaiting_review'
            assert fresh.request['translation_review_pending'] is True
            assert spies.get('dubbing',0)==0 and spies.get('render',0)==0
        else:
            assert job.status!='awaiting_review'
            assert spies.get('dubbing')==1 and spies.get('render')==1
            expected='4 kg.' if finding=='draft' else '3 kg.'
            assert seen['spoken']==expected and expected in seen['caption_texts']
            assert seen['status']==('draft' if finding=='draft' else 'success')
            assert fresh.load_json('qc','report')['status']=='NEEDS_REVIEW'
            assert fresh.record('translation')['metadata']['draft_count']==(1 if finding=='draft' else 0)
