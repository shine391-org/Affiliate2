from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralmint_overlay.publish_service import (
    PublishActionError,
    default_publish_metadata,
    facebook_reel_eligibility,
    launch_publish,
    mark_tiktok_posted,
)
from viralmint_overlay.publish_store import PublishStore, PublishValidationError
from viralmint_overlay.review_store import REMAKE_STAGE, ReviewStore


def _root(tmp_path: Path, *, final: bool = True) -> tuple[ReviewStore, PublishStore]:
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (tmp_path / "radar.py").write_text("# root", encoding="utf-8")
    candidate = {
        "rank": 1,
        "platform": "douyin",
        "content_id": "123",
        "title": "Original title",
        "intelligence": {"localization_potential": "HIGH", "remake_difficulty": "LOW"},
    }
    (data / "candidate_intelligence.json").write_text(
        json.dumps({"candidates": [candidate]}),
        encoding="utf-8",
    )
    cdir = data / "candidates" / "douyin_123"
    cdir.mkdir(parents=True)
    (cdir / "source.mp4").write_bytes(b"source")

    if final:
        rdir = data / "remakes" / "douyin_123"
        rdir.mkdir(parents=True)
        (rdir / "final.mp4").write_bytes(b"final")
        (rdir / "adaptation.json").write_text(
            json.dumps(
                {
                    "vietnamese_adaptation": {
                        "hook": "Món này làm 10 phút là xong",
                        "script": "Đây là phần mô tả tiếng Việt.",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    review = ReviewStore(db_path=data / "radar.db", project_root=tmp_path)
    publish = PublishStore(db_path=data / "radar.db", project_root=tmp_path)
    review.list_candidates()
    return review, publish


def test_default_metadata_prefers_vietnamese_adaptation():
    detail = {
        "title": "Chinese title",
        "adaptation": {
            "vietnamese_adaptation": {
                "hook": "Hook Việt",
                "script": "Mô tả Việt",
            }
        },
    }
    out = default_publish_metadata(detail)
    assert out == {"title": "Hook Việt", "caption": "Mô tả Việt"}


def test_default_metadata_uses_translation_when_no_adaptation():
    detail = {
        "title": "Chinese title",
        "translation": {
            "segments": [
                {"text": "Dòng đầu tiếng Việt"},
                {"text": "Dòng thứ hai"},
            ]
        },
    }
    out = default_publish_metadata(detail)
    assert out["title"] == "Dòng đầu tiếng Việt"
    assert "Dòng thứ hai" in out["caption"]


def test_facebook_reel_eligibility(monkeypatch, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(
        "viralmint_overlay.publish_service.probe_video",
        lambda _: {"width": 1080, "height": 1920, "duration": 55.0, "size": 1, "frame_rate": "30/1"},
    )
    ok, reason, _ = facebook_reel_eligibility(video)
    assert ok is True
    assert reason is None


def test_facebook_reel_rejects_long_video(monkeypatch, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(
        "viralmint_overlay.publish_service.probe_video",
        lambda _: {"width": 1080, "height": 1920, "duration": 75.0, "size": 1, "frame_rate": "30/1"},
    )
    ok, reason, _ = facebook_reel_eligibility(video)
    assert ok is False
    assert "60" in reason


def test_publish_store_records_attempt_and_external_url(tmp_path):
    _, store = _root(tmp_path)
    pub_id = store.create_publication(
        "douyin",
        "123",
        "youtube",
        job_id="job-1",
        title="T",
        caption="C",
        privacy="private",
    )
    store.finish_publication(
        pub_id,
        status="PUBLISHED",
        external_id="video1",
        external_url="https://youtu.be/video1",
    )
    latest = store.latest_publication("douyin", "123", "youtube")
    assert latest["job_id"] == "job-1"
    assert latest["status"] == "PUBLISHED"
    assert latest["external_url"] == "https://youtu.be/video1"


def test_oauth_state_is_one_time(tmp_path):
    _, store = _root(tmp_path)
    state = store.create_oauth_state("youtube")
    assert store.consume_oauth_state(state) is True
    assert store.consume_oauth_state(state) is False


def test_tiktok_manual_mark_requires_final_approval(tmp_path):
    review, publish = _root(tmp_path)
    with pytest.raises(PublishActionError, match="APPROVED"):
        mark_tiktok_posted(
            review,
            publish,
            "douyin",
            "123",
            title="T",
            caption="C",
            external_url="https://www.tiktok.com/@x/video/1",
        )


def test_tiktok_manual_post_is_recorded_after_approval(tmp_path):
    review, publish = _root(tmp_path)
    review.set_decision("douyin", "123", REMAKE_STAGE, "APPROVED")
    result = mark_tiktok_posted(
        review,
        publish,
        "douyin",
        "123",
        title="T",
        caption="C",
        external_url="https://www.tiktok.com/@x/video/1",
    )
    assert result["platform"] == "tiktok"
    assert result["status"] == "PUBLISHED"


def test_tiktok_manual_post_rejects_non_tiktok_url(tmp_path):
    review, publish = _root(tmp_path)
    review.set_decision("douyin", "123", REMAKE_STAGE, "APPROVED")
    with pytest.raises(PublishActionError, match="tiktok.com"):
        mark_tiktok_posted(
            review,
            publish,
            "douyin",
            "123",
            title="T",
            caption="C",
            external_url="https://example.com/not-tiktok",
        )



@pytest.mark.asyncio
async def test_api_publish_requires_final_approval_before_backend_job_import(tmp_path):
    review, publish = _root(tmp_path)
    with pytest.raises(PublishActionError, match="APPROVED"):
        await launch_publish(
            review,
            publish,
            "douyin",
            "123",
            "youtube",
            title="T",
            caption="C",
            privacy="private",
        )


def test_youtube_credentials_are_not_required_for_manual_tiktok(tmp_path):
    review, publish = _root(tmp_path)
    review.set_decision("douyin", "123", REMAKE_STAGE, "APPROVED")
    result = mark_tiktok_posted(
        review,
        publish,
        "douyin",
        "123",
        title="T",
        caption="C",
        external_url="https://tiktok.com/@x/video/2",
    )
    assert result["status"] == "PUBLISHED"



def test_facebook_reel_rejects_low_frame_rate(monkeypatch, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(
        "viralmint_overlay.publish_service.probe_video",
        lambda _: {"width": 1080, "height": 1920, "duration": 30.0, "size": 1, "frame_rate": "20/1"},
    )
    ok, reason, _ = facebook_reel_eligibility(video)
    assert ok is False
    assert "23 FPS" in reason



def test_only_one_active_publish_per_candidate_platform(tmp_path):
    _, store = _root(tmp_path)
    first = store.create_publication(
        "douyin",
        "123",
        "youtube",
        title="T1",
        caption="C1",
        privacy="private",
        status="PUBLISHING",
    )
    assert first > 0

    with pytest.raises(PublishValidationError, match="already running"):
        store.create_publication(
            "douyin",
            "123",
            "youtube",
            title="T2",
            caption="C2",
            privacy="private",
            status="PUBLISHING",
        )

    store.finish_publication(first, status="FAILED", error_message="test")
    second = store.create_publication(
        "douyin",
        "123",
        "youtube",
        title="T3",
        caption="C3",
        privacy="private",
        status="PUBLISHING",
    )
    assert second > first
