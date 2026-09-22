from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralmint_overlay.action_service import (
    ActionValidationError,
    _prepare_staging_dir,
    _promote_staging,
    resolve_requested_action,
)
from viralmint_overlay.review_store import ReviewStore
from src.radar.remake.adaptation import _build_adaptation_prompt


def _detail(
    *,
    candidate_status: str = "APPROVED_FOR_REMAKE",
    has_final: bool = False,
    remake_status: str | None = None,
    note: str | None = None,
    manifest: dict | None = None,
) -> dict:
    return {
        "remake_exists": has_final,
        "candidate_review": {"status": candidate_status},
        "remake_review": (
            {"status": remake_status, "note": note} if remake_status else None
        ),
        "action_manifest": manifest or {},
    }


def test_approved_candidate_can_translate():
    action, params, note = resolve_requested_action(
        "translate",
        _detail(),
        {"translate_mode": "full_dub", "voice_id": "vi-VN-HoaiMyNeural"},
    )
    assert action == "translate"
    assert params["target_language"] == "Vietnamese"
    assert params["translate_mode"] == "full_dub"
    assert params["voice_id"] == "vi-VN-HoaiMyNeural"
    assert note is None


def test_unapproved_candidate_cannot_produce():
    with pytest.raises(ActionValidationError, match="APPROVED_FOR_REMAKE"):
        resolve_requested_action(
            "remake",
            _detail(candidate_status="REVIEW_PENDING"),
            {"target_duration": "20-35s"},
        )


def test_existing_final_requires_rerender_path():
    with pytest.raises(ActionValidationError, match="RERENDER"):
        resolve_requested_action(
            "translate",
            _detail(has_final=True, remake_status="REMAKE_REVIEW"),
            {},
        )


def test_rerender_requires_rework_note():
    with pytest.raises(ActionValidationError, match="REWORK note"):
        resolve_requested_action(
            "rerender",
            _detail(has_final=True, remake_status="REWORK", note=""),
            {},
        )


def test_rerender_repeats_previous_translate_mode_with_overrides():
    action, params, note = resolve_requested_action(
        "rerender",
        _detail(
            has_final=True,
            remake_status="REWORK",
            note="đổi sang giọng nữ",
            manifest={
                "action": "translate",
                "params": {
                    "translate_mode": "full_dub",
                    "voice_id": "vi-VN-NamMinhNeural",
                },
            },
        ),
        {"voice_id": "vi-VN-HoaiMyNeural"},
    )
    assert action == "translate"
    assert params["translate_mode"] == "full_dub"
    assert params["voice_id"] == "vi-VN-HoaiMyNeural"
    assert note == "đổi sang giọng nữ"


def test_legacy_phase32_rerender_falls_back_to_creative_remake():
    action, params, note = resolve_requested_action(
        "rerender",
        _detail(
            has_final=True,
            remake_status="REWORK",
            note="hook chậm",
            manifest={},
        ),
        {"target_duration": "35-60s"},
    )
    assert action == "remake"
    assert params["target_duration"] == "35-60s"
    assert note == "hook chậm"


def test_invalid_voice_rejected():
    with pytest.raises(ActionValidationError, match="voice"):
        resolve_requested_action(
            "remake",
            _detail(),
            {"voice_id": "not-a-voice"},
        )


def test_staging_promotion_replaces_old_without_copy(tmp_path):
    final_dir = tmp_path / "candidate"
    final_dir.mkdir()
    (final_dir / "final.mp4").write_bytes(b"old")

    staging = tmp_path / ".candidate.staging"
    staging.mkdir()
    (staging / "final.mp4").write_bytes(b"new")
    backup = tmp_path / ".candidate.backup"

    _promote_staging(staging, final_dir, backup)

    assert (final_dir / "final.mp4").read_bytes() == b"new"
    assert not staging.exists()
    assert not backup.exists()


def test_review_store_records_latest_action(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "radar.py").write_text("# root", encoding="utf-8")
    (tmp_path / "data" / "candidate_intelligence.json").write_text(
        json.dumps({"candidates": []}),
        encoding="utf-8",
    )
    store = ReviewStore(
        db_path=tmp_path / "data" / "radar.db",
        project_root=tmp_path,
    )

    store.record_action(
        "job-1",
        "douyin",
        "123",
        "remake",
        {"target_duration": "20-35s"},
    )
    store.record_action(
        "job-2",
        "douyin",
        "123",
        "rerender",
        {"target_duration": "35-60s"},
    )

    latest = store.latest_action("douyin", "123")
    assert latest["job_id"] == "job-2"
    assert latest["action"] == "rerender"
    assert latest["params"]["target_duration"] == "35-60s"



def test_rework_note_is_injected_into_creative_prompt():
    prompt = _build_adaptation_prompt(
        {
            "topic": "Food",
            "content_format": {},
            "hook": {},
            "structure": [],
            "vietnamese_angle": {},
        },
        {"segments": [{"text": "source"}]},
        "20-35s",
        review_note="Hook đang chậm, vào món sớm hơn.",
    )
    assert "Hook đang chậm, vào món sớm hơn." in prompt
    assert "HUMAN REWORK FEEDBACK" in prompt



def test_prepare_staging_recovers_backup_if_live_dir_missing(tmp_path):
    remakes = tmp_path / "data" / "remakes"
    remakes.mkdir(parents=True)
    backup = remakes / ".douyin_123.backup-old"
    backup.mkdir()
    (backup / "final.mp4").write_bytes(b"old-final")

    final_dir, staging, new_backup = _prepare_staging_dir(
        tmp_path, "douyin_123", "abcdef123456"
    )

    assert (final_dir / "final.mp4").read_bytes() == b"old-final"
    assert staging.exists()
    assert not backup.exists()
    assert new_backup.name.startswith(".douyin_123.backup-")
