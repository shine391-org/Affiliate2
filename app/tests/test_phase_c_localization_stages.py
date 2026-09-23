# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import localization_stage_service as stages


@pytest.fixture
def stage_root(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "settings", SimpleNamespace(STORAGE_ROOT=tmp_path))
    return tmp_path


def test_manifest_is_valid_json_and_source_is_persisted(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    run = stages.LocalizationRun(
        "job-1",
        source_path=str(source),
        effective_profile={"id": "hq"},
        request={"mode": "full_dub"},
    )
    persisted = run.ensure_source_copy(source)
    assert persisted.exists()
    assert persisted.parent == stage_root / "localization_jobs" / "job-1"
    data = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    assert data["effective_profile"]["id"] == "hq"
    assert Path(data["source_path"]) == persisted.resolve()
    assert data["source_identity"] == {
        "sha256": hashlib.sha256(b"video").hexdigest(),
        "size_bytes": 5,
    }
    assert run.verify_source_identity() == data["source_identity"]


def test_source_overwrite_is_rejected_before_resume(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"original-video")
    run = stages.LocalizationRun("job-source-change", source_path=str(source))
    persisted = run.ensure_source_copy(source)
    run.mark_complete_without_artifacts("separation", metadata={"skipped": True})

    persisted.write_bytes(b"different-video")

    with pytest.raises(RuntimeError, match="has changed"):
        run.verify_source_identity()


def test_same_source_bytes_remain_resumable(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    payload = b"same-video"
    source.write_bytes(payload)
    run = stages.LocalizationRun("job-source-same", source_path=str(source))
    persisted = run.ensure_source_copy(source)
    run.mark_complete_without_artifacts("separation", metadata={"skipped": True})

    persisted.write_bytes(payload)

    got = run.verify_source_identity()
    assert got["sha256"] == hashlib.sha256(payload).hexdigest()


def test_legacy_completed_run_without_source_identity_fails_closed(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"legacy-video")
    run = stages.LocalizationRun("job-legacy-source", source_path=str(source))
    run.ensure_source_copy(source)
    run.mark_complete_without_artifacts("separation", metadata={"skipped": True})

    data = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    data.pop("source_identity", None)
    run.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    loaded = stages.LocalizationRun.load("job-legacy-source")

    with pytest.raises(RuntimeError, match="identity is missing"):
        loaded.verify_source_identity()


def test_full_invalidation_can_accept_intentionally_replaced_source(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video-a")
    run = stages.LocalizationRun("job-source-reset", source_path=str(source))
    persisted = run.ensure_source_copy(source)
    run.mark_complete_without_artifacts("separation", metadata={"skipped": True})

    persisted.write_bytes(b"video-b")
    with pytest.raises(RuntimeError, match="has changed"):
        run.verify_source_identity()

    run.invalidate_from("separation", delete_artifacts=True)
    refreshed = run.refresh_source_identity()

    assert refreshed["sha256"] == hashlib.sha256(b"video-b").hexdigest()
    assert run.verify_source_identity() == refreshed


def test_complete_requires_real_artifact(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"x")
    run = stages.LocalizationRun("job-2", source_path=str(source))
    with pytest.raises(RuntimeError):
        run.complete("asr", artifacts={"transcript": tmp_path / "missing.json"})


def test_resume_requires_complete_artifacts_not_only_status(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"x")
    run = stages.LocalizationRun("job-3", source_path=str(source))
    artifact = run.save_json("asr", "transcript.json", {"text": "hello"})
    run.complete("asr", artifacts={"transcript": artifact})
    assert run.is_complete("asr", ("transcript",))
    artifact.unlink()
    assert not run.is_complete("asr", ("transcript",))


def test_invalidate_from_removes_downstream_and_preserves_upstream(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"x")
    run = stages.LocalizationRun("job-4", source_path=str(source))
    asr = run.save_json("asr", "tx.json", {"ok": 1})
    tr = run.save_json("translation", "tx.json", {"ok": 1})
    run.complete("asr", artifacts={"transcript": asr})
    run.complete("translation", artifacts={"transcript": tr})

    invalidated = run.invalidate_from("translation", delete_artifacts=True)
    assert "translation" in invalidated and "qc" in invalidated
    assert run.record("asr")["status"] == "complete"
    assert run.record("translation")["status"] == "invalidated"
    assert asr.exists()
    assert not tr.exists()


def test_chunk_regeneration_can_invalidate_without_deleting_raw_files(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"x")
    run = stages.LocalizationRun("job-5", source_path=str(source))
    raw = run.artifact_path("dubbing", "chunk_0001.wav")
    raw.write_bytes(b"audio")
    run.complete("dubbing", artifacts={"dub_track": raw})
    run.invalidate_from("dubbing", delete_artifacts=False)
    assert raw.exists()
    assert run.record("dubbing")["status"] == "invalidated"



def test_old_manifest_version_is_rejected_instead_of_resumed(stage_root, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"x")
    run = stages.LocalizationRun("job-old", source_path=str(source))
    data = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    data["version"] = stages.MANIFEST_VERSION - 1
    run.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="incompatible"):
        stages.LocalizationRun.load("job-old")
