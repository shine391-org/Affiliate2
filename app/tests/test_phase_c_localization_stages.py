# SPDX-License-Identifier: AGPL-3.0-only
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
