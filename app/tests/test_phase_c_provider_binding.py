# SPDX-License-Identifier: AGPL-3.0-only
"""Phase-C backend provider-binding UX.

Covers the read model (``provider_binding``) that reports whether a saved
localization job's provider snapshot still matches the live Settings, and the
explicit ``rebind-providers`` adoption subresource.

Real SQLite (Job/UserSettings rows) and a real on-disk localization workspace
are used wherever the behavior under test touches persistence, because the
failure modes that matter here (silent Settings drift mutating an old job,
partial invalidation, a half-applied rebind) only exist at that boundary.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.api import localization as api
from backend.config import Settings
from backend.core.localization import load_profile_store
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job
from backend.models.user_settings import UserSettings
from backend.services import localization_stage_service as stages
from backend.services.localization_service import (
    materialize_localization_profile,
    resolve_localization_profile,
)

UID = "local"
SOURCE_BYTES = b"provider-binding-source-media"


@pytest.mark.asyncio
async def test_fast_asr_rebind_keeps_separation_and_policy(storage, dispatch):
    payload = _profile_payload(pinned=True)
    payload["profiles"][0].update({"asr_model": "large-v3"})
    await _seed_settings(profile_payload=payload)
    await _seed_job("fast-asr-rebind")
    run = await _create_run("fast-asr-rebind", storage=storage)
    _mark_complete(run, "separation", ("vocals", "background"))
    _mark_complete(run, "asr", ("transcript.json",))
    _complete_translation_and_dubbing(run)
    before = run.record("separation").copy()
    run.update_request({**run.request, "regenerate_chunk_ids": [1]})
    run.update_effective_profile({**run.effective_profile, "translation_instructions": "job-only override"})
    payload["profiles"][0]["asr_model"] = "medium"
    await _update_settings(localization_profiles_json=json.dumps(payload))
    result = await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())
    fresh = stages.LocalizationRun.load(run.job_id)
    assert result["from_stage"] == "asr"
    assert result["changed_stages"] == ["asr"]
    assert fresh.record("separation") == before
    assert fresh.effective_profile["asr_model"] == "medium"
    assert fresh.effective_profile["translation_instructions"] == "job-only override"
    assert fresh.request["regenerate_chunk_ids"] == []
    assert not fresh.is_complete("asr")
    assert dispatch.calls == [run.job_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("updates,expected", [({"audio_mode": "fast_duck"}, "separation"), ({"preserve_background": False, "background_gain_db": -15}, "audio_mix")])
async def test_fast_profile_override_real_artifact_invalidation(storage, updates, expected):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    await _seed_job("fast-profile-invalidate")
    run = await _create_run("fast-profile-invalidate", storage=storage)
    _mark_complete(run, "separation", ("vocals", "background"))
    _mark_complete(run, "asr", ("transcript.json",))
    _complete_translation_and_dubbing(run)
    before = {stage: run.record(stage).copy() for stage in stages.STAGES}
    run.update_request({**run.request, "regenerate_chunk_ids": [1], "mask_box": {"custom": True}})
    result = await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(profile_updates=updates))
    fresh = stages.LocalizationRun.load(run.job_id)
    assert result["invalidated_from"] == expected
    for stage in stages.STAGES[:stages.STAGES.index(expected)]:
        assert fresh.record(stage) == before[stage]
    assert not fresh.is_complete(expected)
    assert fresh.request["mask_box"] == {"custom": True}
    if stages.STAGES.index(expected) <= stages.STAGES.index("dubbing"):
        assert not fresh.request.get("regenerate_chunk_ids")


@pytest.mark.asyncio
async def test_speaker_voice_override_is_job_local_revision_bound_and_keeps_translation(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    await _seed_job("speaker-voice-edit")
    run = await _create_run("speaker-voice-edit", storage=storage, review=True)
    _complete_translation_and_dubbing(run)
    translation_before = _snapshot_tree(run, "translation")
    settings_before = (await _load_settings_row()).localization_profiles_json
    run.root.joinpath("dubbing_state.json").write_text(json.dumps({
        "speaker_voices": {"SPEAKER_00": "old-voice"}, "detected_speakers": ["SPEAKER_00"],
    }), encoding="utf-8")
    revision = api._review_revision(run, "translation", "transcript")
    updates = {"speaker_voices": {"SPEAKER_00": "voice-a", "SPEAKER_01": "voice-b"}}
    with pytest.raises(api.HTTPException) as rejected:
        await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(revision="stale", profile_updates=updates))
    assert rejected.value.status_code == 409
    result = await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(revision=revision, profile_updates=updates))
    fresh = stages.LocalizationRun.load(run.job_id)
    assert result["invalidated_from"] == "dubbing"
    assert fresh.effective_profile["speaker_voices"] == updates["speaker_voices"]
    assert api._job_detail(fresh)["speaker_state"]["speaker_voices"] == updates["speaker_voices"]
    assert _snapshot_tree(fresh, "translation") == translation_before
    assert (await _load_settings_row()).localization_profiles_json == settings_before


@pytest.mark.asyncio
async def test_speaker_correction_preserves_word_times_and_secondary_overlap(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    await _seed_job("speaker-correction")
    run = await _create_run("speaker-correction", storage=storage)
    tx = {"text": "Hello", "language": "en", "segments": [{"id": "s1", "source_text": "Hello", "translated_text": "Xin chào",
        "source_start_ms": 0, "source_end_ms": 1000, "speaker_id": "SPEAKER_00", "translation_status": "success",
        "source_words": [{"word": "Hello", "start": 0.2, "end": 0.8, "speaker_id": "SPEAKER_00",
                          "speaker_ids": ["SPEAKER_00", "SPEAKER_01"]}]}]}
    path = run.save_json("translation", "transcript.json", tx)
    run.complete("translation", artifacts={"transcript": path})
    revision = api._review_revision(run, "translation", "transcript")
    await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(revision=revision,
        segments=[api.SegmentOverride(id="s1", speaker_id="corrected")]))
    fresh = stages.LocalizationRun.load(run.job_id)
    segment = fresh.load_json("translation", "transcript")["segments"][0]
    word = segment["source_words"][0]
    assert segment["speaker_id"] == word["speaker_id"] == "corrected"
    assert (word["start"], word["end"]) == (0.2, 0.8)
    assert word["speaker_ids"] == ["SPEAKER_01", "corrected"]
    assert api._job_detail(fresh)["speaker_state"]["detected_speakers"] == ["SPEAKER_01", "corrected"]


# ── schema / storage isolation ──────────────────────────────────────────────

async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(_create_schema())


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: root))
    return root


@pytest.fixture(autouse=True)
def _clean_rows():
    async def _wipe():
        async with AsyncSessionLocal() as db:
            for row in (await db.execute(select(Job))).scalars().all():
                await db.delete(row)
            for row in (await db.execute(
                select(UserSettings).where(UserSettings.user_id == UID)
            )).scalars().all():
                await db.delete(row)
            await db.commit()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


# ── fixtures: real Settings + real run ──────────────────────────────────────

def _profile_payload(*, pinned: bool) -> dict:
    """``pinned`` profile pins routes/TTS; otherwise everything inherits."""
    profile: dict = {"id": "hq", "name": "HQ"}
    if pinned:
        profile.update({
            "translation_routes": [
                {"connection_id": "conn-a", "model": "model-a", "base_url": None}
            ],
            "rewrite_routes": [
                {"connection_id": "conn-a", "model": "rewrite-a", "base_url": None}
            ],
            "tts_connection_id": "tts-a",
            "tts_provider": "edge_tts",
            "tts_voice": "vi-VN-HoaiMyNeural",
        })
    else:
        # Policy that must survive a rebind untouched.
        profile.update({
            "translation_preset": "affiliate_conversational",
            "translation_instructions": "keep my tone",
            "glossary": {"AI": "AI"},
            "dub_speed_accept": 1.2,
            "audio_mode": "fast_duck",
            "render_profile": "balanced",
        })
    return {"default_profile_id": "hq", "profiles": [profile]}


async def _seed_settings(*, profile_payload: dict, **overrides):
    row = UserSettings(user_id=UID)
    row.ai_provider = "compatible"
    row.ai_model = "model-a"
    row.ai_base_url = "http://127.0.0.1:9001/v1"
    row.ai_connection_id = "conn-a"
    row.ai_connections_json = json.dumps({
        "conn-a": {
            "label": "Conn A", "provider": "compatible",
            "model": "model-a", "base_url": "http://127.0.0.1:9001/v1",
            "api_key_encrypted": None,
        },
    })
    row.tts_provider = "edge_tts"
    row.tts_connection_id = "tts-a"
    row.preferred_tts_voice = "vi-VN-HoaiMyNeural"
    row.tts_connections_json = json.dumps({
        "tts-a": {"provider": "edge_tts", "voice": "vi-VN-HoaiMyNeural"},
    })
    row.localization_profiles_json = json.dumps(profile_payload)
    for key, value in overrides.items():
        setattr(row, key, value)
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()
    return row


async def _update_settings(**overrides):
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == UID)
        )).scalar_one()
        for key, value in overrides.items():
            setattr(row, key, value)
        await db.commit()


async def _seed_job(job_id: str, *, status: str = "failed"):
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, user_id=UID, job_type="tool:translate", status=status,
                   title=job_id))
        await db.commit()


async def _load_settings_row():
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(UserSettings).where(UserSettings.user_id == UID)
        )).scalar_one_or_none()


async def _create_run(job_id, *, storage, mode="full_dub", pinned=True,
                      profile_id="hq", review=False, request_extra=None):
    row = await _load_settings_row()
    profile = resolve_localization_profile(row, profile_id)
    materialized = materialize_localization_profile(
        row, profile, target_language="Vietnamese", for_dub=(mode == "full_dub"),
    )
    source = storage / f"{job_id}-origin.mp4"
    source.write_bytes(SOURCE_BYTES)
    request = {
        "target_language": "Vietnamese",
        "mode": mode,
        "voice_id": "",
        "caption_style": "viral",
        "emoji_style": "moderate",
        "profile_id": profile_id,
        "review_translation": review,
    }
    request.update(request_extra or {})
    run = stages.LocalizationRun(
        job_id,
        source_path=str(source),
        effective_profile=materialized.model_dump(),
        request=request,
    )
    run.ensure_source_copy(source)
    return run


def _mark_complete(run, stage: str, names: tuple[str, ...]):
    artifacts = {}
    for name in names:
        if name.endswith(".json"):
            artifacts[name.removesuffix(".json")] = run.save_json(
                stage, name, {"stage": stage, "name": name},
            )
        else:
            path = run.artifact_path(stage, name)
            path.write_bytes(f"{stage}:{name}".encode("utf-8"))
            artifacts[name] = path
    run.complete(stage, artifacts=artifacts)


def _complete_translation_and_dubbing(run):
    _mark_complete(run, "semantic", ("transcript.json",))
    _mark_complete(run, "translation", ("transcript.json",))
    _mark_complete(run, "dubbing", ("transcript.json", "plan.json", "chunks.json", "dub_track.wav"))
    _mark_complete(run, "audio_mix", ("mixed_audio.wav",))
    _mark_complete(run, "final_render", ("final_video.mp4",))
    _mark_complete(run, "qc", ("report.json",))


def _manifest_bytes(run) -> bytes:
    return run.manifest_path.read_bytes()


def _write_sine_wav(path: Path, duration_ms: int, rate: int = 16000) -> Path:
    """A real, decodable WAV so C7's published-artifact measurement can run."""
    import array
    import math
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    count = rate * duration_ms // 1000
    samples = array.array('h', (round(3000 * math.sin(2 * math.pi * 440 * n / rate))
                                for n in range(count)))
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return path


def _snapshot_tree(run, stage: str) -> dict[str, bytes]:
    directory = run.root / stage
    if not directory.exists():
        return {}
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class _NoDispatch:
    """Records dispatches instead of starting the canonical runner."""

    def __init__(self, *, raises: Exception | None = None):
        self.calls: list[str] = []
        self.raises = raises

    async def __call__(self, run, **_kwargs):
        self.calls.append(run.job_id)
        if self.raises is not None:
            raise self.raises


@pytest.fixture
def dispatch(monkeypatch):
    recorder = _NoDispatch()
    monkeypatch.setattr(api, "_dispatch_existing_run", recorder)
    return recorder


# ══════════════════════════════════════════════════════════════════════════
# Read model: provider_binding
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_matching_binding_reports_no_warning(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-bind-match", storage=storage)

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["binding_mismatch"] is False
    assert binding["changed_stages"] == []
    assert binding["from_stage"] is None
    assert binding["current_error"] is None
    assert binding["current"] is not None
    assert binding["job"]["translation_routes"] == binding["current"]["translation_routes"]
    assert binding["job"]["tts"] == binding["current"]["tts"]


@pytest.mark.asyncio
async def test_settings_drift_does_not_mutate_old_job(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-bind-drift", storage=storage)
    _complete_translation_and_dubbing(run)
    before_manifest = _manifest_bytes(run)
    before_translation = _snapshot_tree(run, "translation")
    before_dubbing = _snapshot_tree(run, "dubbing")

    # Live Settings drift: a different active TTS connection/voice.
    await _update_settings(
        tts_connection_id="tts-b",
        preferred_tts_voice="vi-VN-NamMinhNeural",
        tts_connections_json=json.dumps({
            "tts-a": {"provider": "edge_tts", "voice": "vi-VN-HoaiMyNeural"},
            "tts-b": {"provider": "edge_tts", "voice": "vi-VN-NamMinhNeural"},
        }),
    )

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["binding_mismatch"] is True
    assert binding["changed_stages"] == ["tts"]
    assert binding["from_stage"] == "dubbing"
    assert binding["job"]["tts"]["tts_connection_id"] == "tts-a"
    assert binding["current"]["tts"]["tts_connection_id"] == "tts-b"
    # Reading the binding must never touch the persisted job.
    assert _manifest_bytes(run) == before_manifest
    assert _snapshot_tree(run, "translation") == before_translation
    assert _snapshot_tree(run, "dubbing") == before_dubbing
    assert run.effective_profile["tts_connection_id"] == "tts-a"


@pytest.mark.asyncio
async def test_translation_drift_maps_to_semantic_stage(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-bind-translation", storage=storage)

    payload = _profile_payload(pinned=True)
    payload["profiles"][0]["translation_routes"] = [
        {"connection_id": "conn-a", "model": "model-b", "base_url": None}
    ]
    await _update_settings(localization_profiles_json=json.dumps(payload))

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == ["translation"]
    assert binding["from_stage"] == "semantic"
    assert binding["job"]["translation_routes"] == [
        {"connection_id": "conn-a", "model": "model-a", "base_url": None}
    ]
    assert binding["current"]["translation_routes"] == [
        {"connection_id": "conn-a", "model": "model-b", "base_url": None}
    ]
    # A pinned rewrite route is independent of the translation route.
    assert binding["job"]["rewrite_routes"] == binding["current"]["rewrite_routes"]


@pytest.mark.asyncio
async def test_inherited_rewrite_route_follows_active_ai_model(storage):
    """An inherited rewrite route drifts with the active AI model it resolves to."""
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-bind-inherit-rewrite", storage=storage)

    await _update_settings(ai_model="model-b")

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == ["translation", "rewrite"]
    assert binding["from_stage"] == "semantic"


@pytest.mark.asyncio
async def test_rewrite_drift_maps_to_dubbing_and_keeps_translation(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-bind-rewrite", storage=storage)

    payload = _profile_payload(pinned=True)
    payload["profiles"][0]["rewrite_routes"] = [
        {"connection_id": "conn-a", "model": "rewrite-b", "base_url": None}
    ]
    await _update_settings(localization_profiles_json=json.dumps(payload))

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == ["rewrite"]
    assert binding["from_stage"] == "dubbing"
    assert binding["job"]["translation_routes"] == binding["current"]["translation_routes"]


@pytest.mark.asyncio
async def test_all_stages_drift_reports_ordered_changed_stages(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-bind-all", storage=storage)

    payload = _profile_payload(pinned=False)
    payload["profiles"][0].update({
        "translation_routes": [{"connection_id": "conn-a", "model": "model-z", "base_url": None}],
        "rewrite_routes": [{"connection_id": "conn-a", "model": "rewrite-z", "base_url": None}],
        "tts_voice": "vi-VN-NamMinhNeural",
    })
    await _update_settings(
        ai_model="model-z",
        localization_profiles_json=json.dumps(payload),
    )

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == ["translation", "rewrite", "tts"]
    assert binding["from_stage"] == "semantic"


@pytest.mark.asyncio
async def test_non_full_dub_ignores_rewrite_and_tts_drift(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-bind-captions", storage=storage, mode="captions_only")

    # Drift the rewrite route AND the TTS voice. A captions-only job has no
    # dubbing stage, so neither may be reported as a binding change.
    payload = _profile_payload(pinned=True)
    payload["profiles"][0]["rewrite_routes"] = [
        {"connection_id": "conn-a", "model": "rewrite-b", "base_url": None}
    ]
    payload["profiles"][0]["tts_voice"] = "vi-VN-NamMinhNeural"
    await _update_settings(localization_profiles_json=json.dumps(payload))

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == []
    assert binding["binding_mismatch"] is False
    assert binding["job"]["rewrite_routes"] is None
    assert binding["job"]["tts"] is None
    # The stored snapshot really does differ; it is the mode that suppresses it.
    assert run.effective_profile["tts_voice"] == "vi-VN-HoaiMyNeural"
    assert run.effective_profile["rewrite_routes"][0]["model"] == "rewrite-a"


@pytest.mark.asyncio
async def test_endpoint_rotation_is_not_a_mismatch(storage):
    """A saved connection's live endpoint/key is infrastructure, not identity."""
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-bind-endpoint", storage=storage)

    # Same stable connection id, repaired endpoint + rotated key.
    await _update_settings(
        ai_base_url="http://127.0.0.1:9100/v1",
        ai_connections_json=json.dumps({
            "conn-a": {
                "label": "Conn A", "provider": "compatible",
                "model": "model-a", "base_url": "http://127.0.0.1:9100/v1",
                "api_key_encrypted": "rotated-secret",
            },
        }),
        tts_connections_json=json.dumps({
            "tts-a": {"provider": "edge_tts", "voice": "vi-VN-HoaiMyNeural"},
        }),
    )

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == []
    assert binding["binding_mismatch"] is False
    assert "rotated-secret" not in json.dumps(binding)


@pytest.mark.asyncio
async def test_explicit_route_base_url_override_is_part_of_identity(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-bind-override", storage=storage)

    payload = _profile_payload(pinned=True)
    payload["profiles"][0]["translation_routes"] = [
        {"connection_id": "conn-a", "model": "model-a", "base_url": "http://proxy.internal/v1"}
    ]
    await _update_settings(localization_profiles_json=json.dumps(payload))

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["changed_stages"] == ["translation"]
    assert binding["current"]["translation_routes"][0]["base_url"] == "http://proxy.internal/v1"


@pytest.mark.asyncio
async def test_missing_current_profile_is_reported_without_mutation(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-bind-missing", storage=storage)
    before = _manifest_bytes(run)

    await _update_settings(localization_profiles_json=json.dumps({
        "default_profile_id": "other",
        "profiles": [{"id": "other", "name": "Other"}],
    }))

    binding = await api._describe_provider_binding(run, user_id=UID)

    assert binding["binding_mismatch"] is False
    assert binding["current"] is None
    assert binding["current_error"]
    assert "hq" in binding["current_error"]
    assert _manifest_bytes(run) == before


@pytest.mark.asyncio
async def test_job_detail_exposes_provider_binding(storage, monkeypatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-detail-binding", storage=storage)
    await _seed_job("job-detail-binding", status="failed")

    detail = await api.localization_job_detail("job-detail-binding")

    assert detail["provider_binding"]["binding_mismatch"] is False
    assert detail["provider_binding"]["job"]["translation_routes"]
    assert detail["provider_binding"]["from_stage"] is None
    assert "tts_binding" in detail


# ══════════════════════════════════════════════════════════════════════════
# Explicit adoption: POST /jobs/{id}/rebind-providers
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_rebind_providers_adopts_only_binding_fields(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-bindings", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-bindings", status="failed")

    payload = _profile_payload(pinned=False)
    payload["profiles"][0].update({
        "translation_routes": [{"connection_id": "conn-a", "model": "model-b", "base_url": None}],
        "rewrite_routes": [{"connection_id": "conn-a", "model": "rewrite-b", "base_url": None}],
        "tts_voice": "vi-VN-NamMinhNeural",
        "translation_preset": "literal",
        "translation_instructions": "SHOULD NOT LEAK",
        "glossary": {"LEAK": "LEAK"},
        "dub_speed_accept": 1.25,
        "render_profile": "draft",
    })
    await _update_settings(
        ai_model="model-b",
        preferred_tts_voice="vi-VN-NamMinhNeural",
        localization_profiles_json=json.dumps(payload),
    )

    result = await api.rebind_localization_providers(
        "job-rebind-bindings", api.ProviderRebindRequest(source="settings"),
    )

    assert result["ok"] is True
    assert result["from_stage"] == "semantic"
    assert result["changed_stages"] == ["translation", "rewrite", "tts"]
    assert result["awaiting_translation_review"] is False
    assert dispatch.calls == ["job-rebind-bindings"]

    updated = stages.LocalizationRun.load("job-rebind-bindings")
    profile = updated.effective_profile
    # Binding fields adopted from current Settings/profile.
    assert profile["translation_routes"] == [
        {"connection_id": "conn-a", "model": "model-b", "base_url": None}
    ]
    assert profile["translation_model"] == "model-b"
    assert profile["rewrite_routes"] == [
        {"connection_id": "conn-a", "model": "rewrite-b", "base_url": None}
    ]
    assert profile["tts_voice"] == "vi-VN-NamMinhNeural"
    # Policy is NOT copied.
    assert profile["translation_preset"] == "affiliate_conversational"
    assert profile["translation_instructions"] == "keep my tone"
    assert profile["glossary"] == {"AI": "AI"}
    assert profile["dub_speed_accept"] == 1.2
    assert profile["render_profile"] == "balanced"
    assert updated.request["tts_binding_mode"] == "inherit"
    assert updated.request["voice_id"] == "vi-VN-NamMinhNeural"
    assert updated.request["qc_human_approved"] is False
    # Semantic is the earliest dependency: all downstream artifacts are gone.
    assert updated.record("translation")["status"] == "invalidated"
    assert updated.record("dubbing")["status"] == "invalidated"
    assert updated.record("separation")["status"] == "pending"


@pytest.mark.asyncio
async def test_rebind_providers_tts_only_invalidates_dubbing_and_preserves_upstream(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-tts-only", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-tts-only", status="failed")

    translation_bytes = _snapshot_tree(run, "translation")
    await _update_settings(
        tts_connection_id="tts-b",
        preferred_tts_voice="vi-VN-NamMinhNeural",
        tts_connections_json=json.dumps({
            "tts-b": {"provider": "edge_tts", "voice": "vi-VN-NamMinhNeural"},
        }),
    )

    result = await api.rebind_localization_providers(
        "job-rebind-tts-only", api.ProviderRebindRequest(source="settings"),
    )

    assert result["from_stage"] == "dubbing"
    assert result["changed_stages"] == ["tts"]
    updated = stages.LocalizationRun.load("job-rebind-tts-only")
    assert updated.record("translation")["status"] == "complete"
    assert updated.record("dubbing")["status"] == "invalidated"
    assert _snapshot_tree(updated, "translation") == translation_bytes
    assert updated.effective_profile["translation_routes"] == [
        {"connection_id": "conn-a", "model": "model-a", "base_url": None}
    ]


@pytest.mark.asyncio
async def test_rebind_providers_drops_provider_scoped_tts_state(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-rebind-scoped", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-scoped", status="failed")

    profile = run.effective_profile
    profile.update({
        "speaker_voices": {"SPEAKER_00": "eleven-id"},
        "speaker_reference_audio": {"SPEAKER_00": "ref/eleven.wav"},
        "tts_reference_audio": "ref/global.wav",
        "tts_reference_text": "reference transcript",
        "tts_reference_language": "vi",
    })
    run.update_effective_profile(profile)

    await _update_settings(
        tts_provider="elevenlabs",
        tts_connection_id="tts-eleven",
        preferred_tts_voice="eleven-voice",
        tts_model="eleven_multilingual_v2",
        tts_connections_json=json.dumps({
            "tts-eleven": {
                "provider": "elevenlabs", "voice": "eleven-voice",
                "model": "eleven_multilingual_v2",
                "base_url": "https://api.elevenlabs.io/v1",
            },
        }),
    )
    payload = _profile_payload(pinned=True)
    payload["profiles"][0].update({
        "tts_connection_id": "tts-eleven",
        "tts_provider": "elevenlabs",
        "tts_model": "eleven_multilingual_v2",
        "tts_voice": "eleven-voice",
    })
    await _update_settings(localization_profiles_json=json.dumps(payload))

    result = await api.rebind_localization_providers(
        "job-rebind-scoped", api.ProviderRebindRequest(source="settings"),
    )

    assert result["from_stage"] == "dubbing"
    updated = stages.LocalizationRun.load("job-rebind-scoped")
    assert updated.effective_profile["tts_provider"] == "elevenlabs"
    assert updated.effective_profile["speaker_voices"] == {}
    assert updated.effective_profile["speaker_reference_audio"] == {}
    assert updated.effective_profile["tts_reference_audio"] is None
    assert updated.effective_profile["tts_reference_text"] is None
    assert updated.effective_profile["tts_reference_language"] is None
    assert "speaker_voices" in result["cleared_profile_fields"]


@pytest.mark.asyncio
async def test_rebind_providers_non_full_dub_ignores_rewrite_and_tts(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("job-rebind-captions", storage=storage, mode="captions_only")
    _mark_complete(run, "translation", ("transcript.json",))
    await _seed_job("job-rebind-captions", status="failed")

    payload = _profile_payload(pinned=True)
    payload["profiles"][0]["tts_voice"] = "vi-VN-NamMinhNeural"
    await _update_settings(localization_profiles_json=json.dumps(payload))

    result = await api.rebind_localization_providers(
        "job-rebind-captions", api.ProviderRebindRequest(source="settings"),
    )

    assert result["changed_stages"] == []
    assert result["from_stage"] is None
    assert dispatch.calls == []
    updated = stages.LocalizationRun.load("job-rebind-captions")
    # A captions-only job carries no dubbing binding, so TTS drift must not be
    # adopted into it: the profile's own voice survives untouched.
    assert updated.effective_profile["tts_voice"] == "vi-VN-HoaiMyNeural"
    assert updated.effective_profile["tts_connection_id"] == "tts-a"
    assert updated.record("translation")["status"] == "complete"


@pytest.mark.asyncio
async def test_rebind_providers_preserves_translation_review_gate(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run(
        "job-rebind-review", storage=storage,
        review=True,
        request_extra={
            "translation_review_pending": True,
            "translation_review_approved": False,
        },
    )
    _mark_complete(run, "translation", ("transcript.json",))
    await _seed_job("job-rebind-review", status="awaiting_review")

    await _update_settings(
        tts_connection_id="tts-b",
        preferred_tts_voice="vi-VN-NamMinhNeural",
        tts_connections_json=json.dumps({
            "tts-b": {"provider": "edge_tts", "voice": "vi-VN-NamMinhNeural"},
        }),
    )

    result = await api.rebind_localization_providers(
        "job-rebind-review", api.ProviderRebindRequest(source="settings"),
    )

    assert result["from_stage"] == "dubbing"
    assert result["awaiting_translation_review"] is True
    assert dispatch.calls == []
    updated = stages.LocalizationRun.load("job-rebind-review")
    # The reviewed translation is untouched and still awaiting approval.
    assert updated.record("translation")["status"] == "complete"
    assert updated.request["translation_review_pending"] is True
    assert updated.request["translation_review_approved"] is False
    # The claim is released so the row returns to awaiting_review.
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, "job-rebind-review")
        assert job.status == "awaiting_review"


@pytest.mark.asyncio
async def test_rebind_providers_review_gate_with_translation_drift_reruns_review(
    storage, dispatch,
):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run(
        "job-rebind-review-drift", storage=storage,
        review=True,
        request_extra={
            "translation_review_pending": True,
            "translation_review_approved": False,
        },
    )
    _mark_complete(run, "translation", ("transcript.json",))
    await _seed_job("job-rebind-review-drift", status="awaiting_review")

    await _update_settings(ai_model="model-b")

    result = await api.rebind_localization_providers(
        "job-rebind-review-drift", api.ProviderRebindRequest(source="settings"),
    )

    assert result["from_stage"] == "semantic"
    assert result["awaiting_translation_review"] is False
    assert dispatch.calls == ["job-rebind-review-drift"]
    updated = stages.LocalizationRun.load("job-rebind-review-drift")
    assert updated.request["translation_review_pending"] is False
    assert updated.request["translation_review_approved"] is False
    assert updated.request["review_translation"] is True


@pytest.mark.asyncio
async def test_rebind_providers_missing_current_profile_errors_without_mutation(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-missing", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-missing", status="failed")
    before_manifest = _manifest_bytes(run)
    before_dubbing = _snapshot_tree(run, "dubbing")

    await _update_settings(localization_profiles_json=json.dumps({
        "default_profile_id": "other",
        "profiles": [{"id": "other", "name": "Other"}],
    }))

    with pytest.raises(api.HTTPException) as excinfo:
        await api.rebind_localization_providers(
            "job-rebind-missing", api.ProviderRebindRequest(source="settings"),
        )

    assert excinfo.value.status_code == 400
    assert "hq" in str(excinfo.value.detail)
    assert dispatch.calls == []
    assert _manifest_bytes(run) == before_manifest
    assert _snapshot_tree(run, "dubbing") == before_dubbing
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, "job-rebind-missing")
        assert job.status == "failed"


@pytest.mark.asyncio
async def test_rebind_providers_missing_settings_row_errors_without_mutation(storage, dispatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-noconfig", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-noconfig", status="failed")
    before_manifest = _manifest_bytes(run)

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == UID)
        )).scalar_one()
        await db.delete(row)
        await db.commit()

    with pytest.raises(api.HTTPException) as excinfo:
        await api.rebind_localization_providers(
            "job-rebind-noconfig", api.ProviderRebindRequest(source="settings"),
        )

    assert excinfo.value.status_code in {400, 409}
    assert dispatch.calls == []
    assert _manifest_bytes(run) == before_manifest
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, "job-rebind-noconfig")
        assert job.status == "failed"


@pytest.mark.asyncio
async def test_rebind_providers_dispatch_failure_rolls_back_manifest_and_artifacts(
    storage, monkeypatch,
):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-rollback", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-rollback", status="failed")

    before_manifest = _manifest_bytes(run)
    before_translation = _snapshot_tree(run, "translation")
    before_dubbing = _snapshot_tree(run, "dubbing")

    await _update_settings(ai_model="model-b")
    monkeypatch.setattr(
        api, "_dispatch_existing_run",
        _NoDispatch(raises=OSError("dispatch pipe closed")),
    )

    with pytest.raises(api.HTTPException, match="khôi phục"):
        await api.rebind_localization_providers(
            "job-rebind-rollback", api.ProviderRebindRequest(source="settings"),
        )

    reloaded = stages.LocalizationRun.load("job-rebind-rollback")
    assert _manifest_bytes(reloaded) == before_manifest
    assert _snapshot_tree(reloaded, "translation") == before_translation
    assert _snapshot_tree(reloaded, "dubbing") == before_dubbing
    assert reloaded.effective_profile["translation_routes"] == [
        {"connection_id": "conn-a", "model": "model-a", "base_url": None}
    ]
    assert reloaded.record("dubbing")["status"] == "complete"
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, "job-rebind-rollback")
        assert job.status == "failed"
    # No staging residue is left behind in the workspace.
    assert not (reloaded.root / ".rollback").exists()


@pytest.mark.asyncio
async def test_rebind_providers_invalidation_failure_rolls_back_artifacts(
    storage, monkeypatch,
):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-invalidate-fail", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-invalidate-fail", status="failed")

    before_manifest = _manifest_bytes(run)
    before_dubbing = _snapshot_tree(run, "dubbing")

    await _update_settings(ai_model="model-b")

    real_invalidate = stages.LocalizationRun.invalidate_from

    def exploding_invalidate(self, stage, **kwargs):
        # Prove the rollback restores a HALF-applied invalidation.
        real_invalidate(self, stage, **kwargs)
        raise OSError("disk went away mid-invalidation")

    monkeypatch.setattr(stages.LocalizationRun, "invalidate_from", exploding_invalidate)

    with pytest.raises(api.HTTPException, match="khôi phục"):
        await api.rebind_localization_providers(
            "job-rebind-invalidate-fail", api.ProviderRebindRequest(source="settings"),
        )

    # monkeypatch restores the real method at teardown; loading the manifest
    # does not call invalidate_from, so the persisted state can be read here.
    reloaded = stages.LocalizationRun.load("job-rebind-invalidate-fail")
    assert _manifest_bytes(reloaded) == before_manifest
    assert _snapshot_tree(reloaded, "dubbing") == before_dubbing
    assert reloaded.record("translation")["status"] == "complete"
    assert reloaded.record("dubbing")["status"] == "complete"
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, "job-rebind-invalidate-fail")
        assert job.status == "failed"


@pytest.mark.asyncio
async def test_rebind_providers_rejects_unknown_source(storage):
    with pytest.raises(Exception):
        api.ProviderRebindRequest(source="job")


@pytest.mark.asyncio
async def test_rebind_tts_still_works_with_real_run(storage, dispatch, monkeypatch):
    """The legacy rebind-tts subresource stays compatible."""
    from backend.services import tts_service

    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-rebind-tts-compat", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-rebind-tts-compat", status="failed")

    async def healthy(**_kwargs):
        return {"ok": True}

    monkeypatch.setattr(tts_service, "test_tts_connection", healthy)

    result = await api.rebind_localization_tts(
        "job-rebind-tts-compat", api.TTSRebindRequest(source="settings"),
    )

    assert result["from_stage"] == "dubbing"
    updated = stages.LocalizationRun.load("job-rebind-tts-compat")
    assert updated.request["tts_binding_mode"] == "inherit"
    assert updated.record("translation")["status"] == "complete"
    assert updated.record("dubbing")["status"] == "invalidated"


@pytest.mark.asyncio
async def test_default_rerun_preserves_job_snapshot_when_settings_drifted(
    storage, dispatch,
):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-default-rerun", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job("job-default-rerun", status="failed")
    before_profile = dict(run.effective_profile)

    await _update_settings(
        tts_connection_id="tts-b",
        preferred_tts_voice="vi-VN-NamMinhNeural",
        ai_model="model-b",
        tts_connections_json=json.dumps({
            "tts-b": {"provider": "edge_tts", "voice": "vi-VN-NamMinhNeural"},
        }),
    )

    result = await api.rerun_localization_job(
        "job-default-rerun", api.RerunRequest(from_stage="dubbing"),
    )

    assert result["ok"] is True
    assert result["from_stage"] == "dubbing"
    updated = stages.LocalizationRun.load("job-default-rerun")
    assert updated.effective_profile == before_profile
    assert dispatch.calls == ["job-default-rerun"]


@pytest.mark.asyncio
async def test_binding_uses_snapshot_profile_id_when_request_inherited_default(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-default-profile", storage=storage)
    run.update_request({**run.request, "profile_id": None})
    payload = _profile_payload(pinned=False)
    payload["profiles"].append({"id": "other", "name": "Other", "translation_model": "different"})
    payload["default_profile_id"] = "other"
    await _update_settings(localization_profiles_json=json.dumps(payload))
    binding = await api._describe_provider_binding(run)
    assert binding["current_error"] is None
    assert not binding["binding_mismatch"]


def test_transaction_keeps_backup_if_restore_fails(storage, tmp_path, monkeypatch):
    source = tmp_path / "restore.mp4"
    source.write_bytes(SOURCE_BYTES)
    run = stages.LocalizationRun("job-restore-fail", source_path=str(source))
    _complete_translation_and_dubbing(run)
    txn = stages.ManifestTransaction(run, ["dubbing"])
    txn.begin()
    run.invalidate_from("dubbing")

    def fail_copy(*args, **kwargs):
        raise OSError("restore unavailable")

    with monkeypatch.context() as patcher:
        patcher.setattr(stages.shutil, "copytree", fail_copy)
        with pytest.raises(OSError, match="restore unavailable"):
            txn.rollback()
    assert (txn.backup_root / "manifest.json").is_file()
    assert (txn.backup_root / "stages" / "dubbing").is_dir()
    txn.rollback()
    assert run.record("dubbing")["status"] == "complete"


@pytest.mark.asyncio
async def test_rebind_io_failure_returns_actionable_http_error(storage, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-http-rollback", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job(run.job_id)
    before = _manifest_bytes(run)
    artifacts = _snapshot_tree(run, "dubbing")
    await _update_settings(ai_model="model-b")
    monkeypatch.setattr(api, "_dispatch_existing_run", _NoDispatch(raises=OSError("queue unavailable")))
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.post(f"/api/localization/jobs/{run.job_id}/rebind-providers", json={"source": "settings"})
    assert response.status_code == 503
    assert "khôi phục" in response.json()["detail"]
    assert _manifest_bytes(run) == before
    assert _snapshot_tree(run, "dubbing") == artifacts


@pytest.mark.asyncio
async def test_rebind_rejects_incomplete_artifact_deletion(storage, dispatch, monkeypatch):
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("job-delete-incomplete", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job(run.job_id)
    before = _manifest_bytes(run)
    await _update_settings(ai_model="model-b")
    invalidate = stages.LocalizationRun.invalidate_from

    def leave_files(self, stage, **kwargs):
        return invalidate(self, stage, delete_artifacts=False)

    monkeypatch.setattr(stages.LocalizationRun, "invalidate_from", leave_files)
    with pytest.raises(api.HTTPException):
        await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())
    assert dispatch.calls == []
    assert _manifest_bytes(run) == before


def test_transaction_prepare_failure_leaves_original_and_allows_retry(storage, tmp_path, monkeypatch):
    source = tmp_path / "prepare.mp4"
    source.write_bytes(SOURCE_BYTES)
    run = stages.LocalizationRun("job-prepare-fail", source_path=str(source))
    _complete_translation_and_dubbing(run)
    before = _manifest_bytes(run)
    txn = stages.ManifestTransaction(run, ["dubbing"])

    def fail_copy(*args, **kwargs):
        raise OSError("backup storage full")

    with monkeypatch.context() as patcher:
        patcher.setattr(stages.shutil, "copytree", fail_copy)
        with pytest.raises(OSError):
            txn.begin()
    assert _manifest_bytes(run) == before
    assert not txn.backup_root.exists()
    txn.begin()
    txn.commit()


# ══════════════════════════════════════════════════════════════════════════
# Stage service transaction support
# ══════════════════════════════════════════════════════════════════════════

def test_manifest_transaction_restores_manifest_and_artifacts(storage, tmp_path):
    source = tmp_path / "src.mp4"
    source.write_bytes(SOURCE_BYTES)
    run = stages.LocalizationRun("job-txn", source_path=str(source))
    run.ensure_source_copy(source)
    _complete_translation_and_dubbing(run)
    before_manifest = _manifest_bytes(run)
    before_dubbing = _snapshot_tree(run, "dubbing")

    txn = stages.ManifestTransaction(run, stages.STAGES[stages.STAGES.index("translation"):])
    txn.begin()
    run.invalidate_from("translation", delete_artifacts=True)
    assert run.record("dubbing")["status"] == "invalidated"
    txn.rollback()

    restored = stages.LocalizationRun.load("job-txn")
    assert _manifest_bytes(restored) == before_manifest
    assert _snapshot_tree(restored, "dubbing") == before_dubbing
    assert restored.record("translation")["status"] == "complete"
    assert not (restored.root / ".rollback").exists()


def test_manifest_transaction_commit_discards_backups(storage, tmp_path):
    source = tmp_path / "src.mp4"
    source.write_bytes(SOURCE_BYTES)
    run = stages.LocalizationRun("job-txn-commit", source_path=str(source))
    run.ensure_source_copy(source)
    _complete_translation_and_dubbing(run)

    txn = stages.ManifestTransaction(run, ["translation", "dubbing"])
    txn.begin()
    run.invalidate_from("translation", delete_artifacts=True)
    txn.commit()

    assert not (run.root / ".rollback").exists()
    assert not (run.root / "dubbing").exists()
    reloaded = stages.LocalizationRun.load("job-txn-commit")
    assert reloaded.record("translation")["status"] == "invalidated"


@pytest.fixture(autouse=True)
def tts_health(monkeypatch):
    from backend.services import tts_service
    calls = []

    async def healthy(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(tts_service, "test_tts_connection", healthy)
    return calls


@pytest.mark.parametrize("field,value,stage", [
    ("translation_provider", "other", "semantic"),
    ("translation_model", "other-model", "semantic"),
    ("translation_fallback_provider", "fallback", "semantic"),
    ("translation_fallback_model", "fallback-model", "semantic"),
    ("translation_routes", [{"connection_id": "a", "model": "a"}, {"connection_id": "b", "model": "b"}], "semantic"),
    ("rewrite_model", "rewrite-new", "dubbing"),
    ("tts_voice", "new-voice", "dubbing"),
])
def test_binding_diff_uses_profile_dependency_contract(field, value, stage):
    old = {"translation_provider": "a", "translation_model": "a",
           "translation_fallback_provider": "old-fallback"}
    current = {**old, field: value}
    assert api._binding_diff(old, current, is_dub=True)["from_stage"] == stage
    assert api._profile_invalidation({field}) == stage


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["primary", "model", "fallback"])
async def test_translation_rebind_removes_semantic_and_all_downstream(storage, dispatch, tts_health, drift):
    payload = _profile_payload(pinned=True)
    await _seed_settings(profile_payload=payload)
    run = await _create_run("semantic-drift", storage=storage)
    _mark_complete(run, "asr", ("transcript.json",))
    _complete_translation_and_dubbing(run)
    before_asr = _snapshot_tree(run, "asr")
    await _seed_job(run.job_id)
    route = payload["profiles"][0]["translation_routes"][0]
    if drift == "primary":
        route["connection_id"] = "conn-b"
    elif drift == "model":
        route["model"] = "new-model"
    else:
        payload["profiles"][0]["translation_routes"].append({"connection_id": "conn-b", "model": "fallback"})
    row = await _load_settings_row()
    connections = json.loads(row.ai_connections_json)
    connections["conn-b"] = {"provider": "compatible", "model": "fallback", "base_url": "http://127.0.0.1:9002/v1"}
    await _update_settings(ai_connections_json=json.dumps(connections), localization_profiles_json=json.dumps(payload))
    result = await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())
    updated = stages.LocalizationRun.load(run.job_id)
    assert result["from_stage"] == "semantic"
    for stage in stages.STAGES[stages.STAGES.index("semantic"):]:
        assert updated.record(stage)["status"] == "invalidated"
        assert not (updated.root / stage).exists()
    assert _snapshot_tree(updated, "asr") == before_asr
    assert dispatch.calls == [run.job_id]
    assert tts_health == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "awaiting_review"])
@pytest.mark.parametrize("failure", ["rejected", "credential", "transport", "cancelled"])
async def test_tts_preflight_preserves_every_byte_and_releases_claim(storage, dispatch, monkeypatch, status, failure):
    import httpx
    from fastapi import HTTPException
    from backend.core.exceptions import VoiceGenerationError
    from backend.services import tts_service
    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("preflight-failure", storage=storage, review=True,
                            request_extra={"translation_review_pending": status == "awaiting_review"})
    _complete_translation_and_dubbing(run)
    await _seed_job(run.job_id, status=status)
    await _update_settings(preferred_tts_voice="vi-VN-NamMinhNeural", ai_model="new-model")
    before = {str(p.relative_to(run.root)): p.read_bytes() for p in run.root.rglob("*") if p.is_file()}

    async def unhealthy(**kwargs):
        assert kwargs["voice_id"] == "vi-VN-NamMinhNeural"
        if failure == "credential":
            raise VoiceGenerationError("Bad credential")
        if failure == "transport":
            raise httpx.ConnectTimeout("unreachable")
        if failure == "cancelled":
            raise asyncio.CancelledError()
        return {"ok": False, "message": "Select a valid TTS model in Settings"}

    def forbidden_begin(self):
        pytest.fail("Transaction must not begin before successful TTS preflight")

    monkeypatch.setattr(tts_service, "test_tts_connection", unhealthy)
    monkeypatch.setattr(stages.ManifestTransaction, "begin", forbidden_begin)
    expected = asyncio.CancelledError if failure == "cancelled" else HTTPException
    with pytest.raises(expected) as error:
        await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())
    if failure != "cancelled":
        assert error.value.status_code == 400
        assert "Settings" in error.value.detail
    after = {str(p.relative_to(run.root)): p.read_bytes() for p in run.root.rglob("*") if p.is_file()}
    assert after == before
    assert dispatch.calls == []
    assert await api._job_status(run.job_id) == status
    # Prove the claim can be acquired again, rather than merely checking status.
    claim = await api._claim_job_mutation(run.job_id)
    await api._restore_job_mutation(run.job_id, claim)


@pytest.mark.asyncio
async def test_preflight_targets_profile_connection_not_global_default(storage, dispatch, tts_health):
    payload = _profile_payload(pinned=True)
    await _seed_settings(profile_payload=payload)
    run = await _create_run("pinned-preflight", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job(run.job_id)
    payload["profiles"][0].update({
        "tts_connection_id": "tts-profile", "tts_provider": "openai_compatible_tts",
        "tts_model": "profile-model", "tts_voice": "profile-voice",
        "tts_base_url": "http://127.0.0.1:9999/custom", "tts_region": "profile-region",
    })
    await _update_settings(localization_profiles_json=json.dumps(payload), tts_connections_json=json.dumps({
        "tts-profile": {"provider": "openai_compatible_tts", "base_url": "http://127.0.0.1:9998/v1"},
    }))
    result = await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())
    assert len(tts_health) == 1
    checked = tts_health[0]
    assert checked["provider"].value == "openai_compatible_tts"
    assert checked["model"] == "profile-model"
    assert checked["voice_id"] == "profile-voice"
    assert checked["base_url"] == "http://127.0.0.1:9999/custom"
    assert checked["region"] == "profile-region"
    assert result["from_stage"] == "dubbing"
    assert dispatch.calls == [run.job_id]
    assert stages.LocalizationRun.load(run.job_id).effective_profile["tts_connection_id"] == "tts-profile"


@pytest.mark.asyncio
async def test_no_drift_rebind_is_byte_preserving_noop(storage, dispatch, tts_health):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    run = await _create_run("noop-rebind", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job(run.job_id)
    before = _manifest_bytes(run)
    result = await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())
    assert result["from_stage"] is None
    assert result["changed_stages"] == []
    assert _manifest_bytes(run) == before
    assert dispatch.calls == tts_health == []
    assert await api._job_status(run.job_id) == "failed"


@pytest.mark.asyncio
async def test_provider_rebind_uses_canonical_localization_tts_preflight(
    storage, dispatch, monkeypatch, tts_health,
):
    from backend.services import localization_service

    await _seed_settings(profile_payload=_profile_payload(pinned=False))
    run = await _create_run("canonical-preflight", storage=storage)
    _complete_translation_and_dubbing(run)
    await _seed_job(run.job_id)
    await _update_settings(preferred_tts_voice="vi-VN-NamMinhNeural")
    before = {str(p.relative_to(run.root)): p.read_bytes() for p in run.root.rglob("*") if p.is_file()}
    calls = []

    async def reject(user_settings, profile, target_language):
        calls.append((user_settings.user_id, profile.tts_voice, target_language))
        raise ValueError("canonical provider preflight rejected this binding")

    monkeypatch.setattr(localization_service, "validate_localization_tts_preflight", reject)
    with pytest.raises(api.HTTPException) as error:
        await api.rebind_localization_providers(run.job_id, api.ProviderRebindRequest())

    assert error.value.status_code == 400
    assert calls == [("local", "vi-VN-NamMinhNeural", "Vietnamese")]
    after = {str(p.relative_to(run.root)): p.read_bytes() for p in run.root.rglob("*") if p.is_file()}
    assert after == before
    assert dispatch.calls == []
    assert tts_health == []


@pytest.mark.asyncio
async def test_combined_voice_and_bad_segment_override_is_atomic_including_root_state(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    await _seed_job("atomic-speaker-override")
    run = await _create_run("atomic-speaker-override", storage=storage)
    _complete_translation_and_dubbing(run)
    state = run.root / "dubbing_state.json"
    state.write_text(json.dumps({"speaker_voices": {"SPEAKER_00": "old"}, "measurements": {"one": {"text": "Hello"}}}))
    before = run.snapshot()
    state_before = state.read_bytes()
    files_before = {stage: _snapshot_tree(run, stage) for stage in stages.STAGES}
    with pytest.raises(api.HTTPException) as rejected:
        await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(
            revision=api._review_revision(run, "translation", "transcript"),
            profile_updates={"speaker_voices": {"SPEAKER_00": "new"}, "speaker_genders": {"SPEAKER_00": "male"}},
            segments=[api.SegmentOverride(id="missing", translated_text="Xin chào")]))
    assert rejected.value.status_code == 404
    fresh = stages.LocalizationRun.load(run.job_id)
    assert fresh.snapshot() == before and state.read_bytes() == state_before
    assert {stage: _snapshot_tree(fresh, stage) for stage in stages.STAGES} == files_before
    assert not (run.root / ".rollback").exists()
    async with AsyncSessionLocal() as db:
        assert (await db.get(Job, run.job_id)).status == "failed"


@pytest.mark.asyncio
async def test_gender_override_requires_current_translation_and_keeps_settings_and_c6(storage):
    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    await _seed_job("job-gender-override")
    run = await _create_run("job-gender-override", storage=storage)
    _complete_translation_and_dubbing(run)
    before_c6 = _snapshot_tree(run, "translation")
    before_settings = (await _load_settings_row()).localization_profiles_json
    update = {"speaker_genders": {"SPEAKER_00": "male"}, "voice_genders": {"opaque-voice": "male"}}
    with pytest.raises(api.HTTPException) as rejected:
        await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(revision="stale", profile_updates=update))
    assert rejected.value.status_code == 409
    await api.localization_expert_override(run.job_id, api.ExpertOverrideRequest(
        revision=api._review_revision(run, "translation", "transcript"), profile_updates=update))
    fresh = stages.LocalizationRun.load(run.job_id)
    assert fresh.effective_profile["speaker_genders"] == update["speaker_genders"]
    assert _snapshot_tree(fresh, "translation") == before_c6
    assert (await _load_settings_row()).localization_profiles_json == before_settings


@pytest.mark.asyncio
async def test_canonical_auto_retry_preserves_raw_neighbors_accepted_text_and_approved_c6(storage, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.core import tool_runners as runner
    from backend.core.localization import LocalizationTranscript
    from backend.services import localization_service as svc
    from tests.test_localization_measured_speakers import transcript, mock_dubbing

    await _seed_settings(profile_payload=_profile_payload(pinned=True))
    await _seed_job("retry-accepted-dub")
    run = await _create_run("retry-accepted-dub", storage=storage, request_extra={"auto_repair": True})
    run.update_effective_profile({**run.effective_profile, "audio_mode": "fast_duck", "video_retime_mode": "off"})
    run.mark_complete_without_artifacts("separation")
    tx = transcript(("Xin chào tất cả mọi người", "Cảm ơn"), speakers=False)
    for stage in ("asr", "semantic", "translation"):
        path = run.save_json(stage, "transcript.json", tx.model_dump())
        run.complete(stage, artifacts={"transcript": path})
    before_c6 = _snapshot_tree(run, "translation")
    events = []
    mock_dubbing(monkeypatch)
    async def generate(text, *, output_path, **_kwargs):
            events.append(text)
            if len(events) == 3:
                raise TimeoutError("transient provider failure after accepted rewrite")
            # Real decodable audio: C7 measures the PUBLISHED artifact (peak level
            # via ffmpeg astats) and fails closed on undecodable bytes, so a
            # byte placeholder is no longer a valid provider fixture here.
            duration_ms = 1800 if len(events) == 1 else 800
            _write_sine_wav(Path(output_path), duration_ms)
            return duration_ms
    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    rewrites = []
    async def shorten(*_args, **_kwargs):
        rewrites.append(True)
        return '[{"id":"s0","text":"Xin chào"}]'
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", shorten)
    async def forbidden(*_args, **_kwargs): pytest.fail("Retry must not rerun completed upstream stages")
    for name in ("transcribe_for_localization", "prepare_semantic_transcript", "translate_transcript"):
        monkeypatch.setattr(svc, name, forbidden)
    async def mix(source, audio, destination, *_args, **_kwargs): destination.write_bytes(b"mixed")
    monkeypatch.setattr(svc, "mix_dub_audio", mix)
    async def captions(*_args, output_path, **_kwargs):
        output_path.write_text("[Events]\n")
        return output_path
    async def render(source, captions, output, **_kwargs):
        output.write_bytes(b"rendered")
        return output
    monkeypatch.setattr("backend.services.caption_service.generate_captions_ass", captions)
    monkeypatch.setattr("backend.services.caption_service.burn_captions", render)
    monkeypatch.setattr("backend.services.localization_qc_service.evaluate_localization_qc", lambda **_kwargs:
                        SimpleNamespace(as_dict=lambda: {"status": "NEEDS_REVIEW", "failures": [], "warnings": []}))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock())
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())
    await runner.run_tool_translate(run.job_id, run.source_path, "Vietnamese", "full_dub", "", "viral", "none", cleanup_input=False)
    fresh = stages.LocalizationRun.load(run.job_id)
    assert fresh.request["auto_repair_attempts"]["dubbing"] == 1
    assert events == ["Xin chào tất cả mọi người", "Cảm ơn", "Xin chào", "Xin chào"]
    assert len(rewrites) == 1
    assert _snapshot_tree(fresh, "translation") == before_c6
    assert fresh.is_complete("dubbing", ("dub_track", "transcript", "rewrite_audit"))
    dubbed = LocalizationTranscript.model_validate(fresh.load_json("dubbing", "transcript"))
    assert dubbed.segments[0].translated_text == "Xin chào"
    assert fresh.record("dubbing")["metadata"]["rewrite_count"] == 1
