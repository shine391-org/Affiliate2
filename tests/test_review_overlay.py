from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralmint_overlay.review_store import (
    CANDIDATE_STAGE,
    REMAKE_STAGE,
    ReviewStore,
    ReviewValidationError,
)


def _candidate(platform: str, content_id: str, title: str = "Test") -> dict:
    return {
        "platform": platform,
        "content_id": content_id,
        "title": title,
        "creator": "Creator",
        "url": "https://example.com",
        "radar_score": 88.0,
        "radar_confidence": 0.8,
        "radar_breakdown": {"freshness": 80.0, "engagement": 90.0},
        "intelligence": {
            "localization_potential": "HIGH",
            "remake_difficulty": "LOW",
            "risks": {
                "copyright_dependency": "LOW",
                "personality_dependency": "MEDIUM",
            },
        },
    }


def _store(tmp_path: Path, candidates: list[dict]) -> ReviewStore:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "radar.py").write_text("# marker", encoding="utf-8")
    (tmp_path / "data" / "candidate_intelligence.json").write_text(
        json.dumps({"candidates": candidates}, ensure_ascii=False),
        encoding="utf-8",
    )
    return ReviewStore(db_path=tmp_path / "data" / "radar.db", project_root=tmp_path)


def test_seed_pending_candidate(tmp_path):
    store = _store(tmp_path, [_candidate("douyin", "123")])
    result = store.list_candidates()
    assert result["summary"]["pending_review"] == 1
    assert result["candidates"][0]["review_status"] == "REVIEW_PENDING"


def test_seed_existing_remake_as_review_pending(tmp_path):
    store = _store(tmp_path, [_candidate("bilibili", "456")])
    rdir = tmp_path / "data" / "remakes" / "bilibili_456"
    rdir.mkdir(parents=True)
    (rdir / "final.mp4").write_bytes(b"fake")
    result = store.list_candidates()
    assert result["summary"]["rendered"] == 1
    assert result["summary"]["awaiting_remake_review"] == 1
    assert result["candidates"][0]["review_status"] == "REMAKE_REVIEW"


def test_approve_persists_across_store_restart(tmp_path):
    store = _store(tmp_path, [_candidate("douyin", "123")])
    store.list_candidates()
    store.set_decision("douyin", "123", CANDIDATE_STAGE, "APPROVED_FOR_REMAKE", "good")
    reopened = ReviewStore(db_path=store.db_path, project_root=tmp_path)
    review = reopened.get_review("douyin", "123", CANDIDATE_STAGE)
    assert review["status"] == "APPROVED_FOR_REMAKE"
    assert review["note"] == "good"


def test_invalid_state_rejected(tmp_path):
    store = _store(tmp_path, [_candidate("douyin", "123")])
    with pytest.raises(ReviewValidationError):
        store.set_decision("douyin", "123", CANDIDATE_STAGE, "APPROVED")


def test_rework_requires_note(tmp_path):
    store = _store(tmp_path, [_candidate("bilibili", "456")])
    rdir = tmp_path / "data" / "remakes" / "bilibili_456"
    rdir.mkdir(parents=True)
    (rdir / "final.mp4").write_bytes(b"fake")
    with pytest.raises(ReviewValidationError, match="requires a review note"):
        store.set_decision("bilibili", "456", REMAKE_STAGE, "REWORK", "")


def test_rework_note_persists(tmp_path):
    store = _store(tmp_path, [_candidate("bilibili", "456")])
    rdir = tmp_path / "data" / "remakes" / "bilibili_456"
    rdir.mkdir(parents=True)
    (rdir / "final.mp4").write_bytes(b"fake")
    review = store.set_decision(
        "bilibili", "456", REMAKE_STAGE, "REWORK", "voice too robotic"
    )
    assert review["status"] == "REWORK"
    assert review["note"] == "voice too robotic"


def test_media_path_is_confined(tmp_path):
    store = _store(tmp_path, [_candidate("douyin", "123")])
    cdir = tmp_path / "data" / "candidates" / "douyin_123"
    cdir.mkdir(parents=True)
    (cdir / "source.mp4").write_bytes(b"fake")
    assert store.resolve_media_path("source", "douyin", "123") == (cdir / "source.mp4").resolve()
    with pytest.raises(ReviewValidationError):
        store.resolve_media_path("source", "douyin", "../escape")


def test_detail_loads_cached_artifacts(tmp_path):
    store = _store(tmp_path, [_candidate("douyin", "123")])
    cdir = tmp_path / "data" / "candidates" / "douyin_123"
    cdir.mkdir(parents=True)
    (cdir / "metadata.json").write_text('{"ok": true}', encoding="utf-8")
    (cdir / "transcript.json").write_text('{"full_transcript": "hello"}', encoding="utf-8")
    detail = store.get_candidate_detail("douyin", "123")
    assert detail["metadata"]["ok"] is True
    assert detail["transcript"]["full_transcript"] == "hello"



def test_sync_registry_classifier_preserves_custom_file(tmp_path):
    """Custom ViralMint plugin registries must never be mistaken for removable stubs."""
    import importlib.util

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "sync_review_overlay.py"
    spec = importlib.util.spec_from_file_location("sync_review_overlay", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    missing = tmp_path / "missing.js"
    assert module._registry_kind(missing) == "missing"

    stub = tmp_path / "stub.js"
    stub.write_text(
        "export const pluginRoutes = []\nexport const pluginNavItems = []\n",
        encoding="utf-8",
    )
    assert module._registry_kind(stub) == "stub"

    ours = tmp_path / "ours.jsx"
    ours.write_text(
        "// CONTENT_ENGINE_REVIEW_OVERLAY\nexport const pluginRoutes = []\n",
        encoding="utf-8",
    )
    assert module._registry_kind(ours) == "ours"

    custom = tmp_path / "custom.js"
    custom.write_text(
        "export const pluginRoutes = [{ path: 'cron', element: Cron }]\n"
        "export const pluginNavItems = [{ to: '/cron' }]\n",
        encoding="utf-8",
    )
    assert module._registry_kind(custom) == "custom"
    assert custom.exists()
