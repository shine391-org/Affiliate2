from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralmint_overlay.performance_service import (
    build_feedback_summary,
    derived_metrics,
    sync_youtube_metrics,
)
from viralmint_overlay.performance_store import PerformanceStore, PerformanceValidationError
from viralmint_overlay.publish_store import PublishStore
from viralmint_overlay.review_store import REMAKE_STAGE, ReviewStore


def _setup(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (tmp_path / "radar.py").write_text("# root", encoding="utf-8")
    (data / "candidate_intelligence.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "rank": 1,
                        "platform": "douyin",
                        "content_id": "123",
                        "title": "Candidate",
                        "intelligence": {
                            "topic": "low-cal meal",
                            "content_format": {"type": "recipe"},
                            "hook": {"type": "payoff-first"},
                            "localization_potential": "HIGH",
                            "remake_difficulty": "LOW",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cdir = data / "candidates" / "douyin_123"
    cdir.mkdir(parents=True)
    (cdir / "source.mp4").write_bytes(b"source")
    rdir = data / "remakes" / "douyin_123"
    rdir.mkdir(parents=True)
    (rdir / "final.mp4").write_bytes(b"final")

    review = ReviewStore(db_path=data / "radar.db", project_root=tmp_path)
    publish = PublishStore(db_path=data / "radar.db", project_root=tmp_path)
    performance = PerformanceStore(db_path=data / "radar.db", project_root=tmp_path)
    review.list_candidates()
    review.set_decision("douyin", "123", REMAKE_STAGE, "APPROVED")
    return review, publish, performance


def _published(publish: PublishStore, platform: str = "tiktok", external_id: str | None = None) -> int:
    pub_id = publish.create_publication(
        "douyin",
        "123",
        platform,
        title="Title",
        caption="Caption",
        privacy="public" if platform == "youtube" else None,
        status="PUBLISHED",
    )
    publish.finish_publication(
        pub_id,
        status="PUBLISHED",
        external_id=external_id,
        external_url=(
            "https://youtu.be/" + external_id
            if platform == "youtube" and external_id
            else "https://www.tiktok.com/@x/video/1"
        ),
    )
    return pub_id


def test_manual_snapshot_requires_non_negative_metric(tmp_path):
    _, publish, performance = _setup(tmp_path)
    pub_id = _published(publish)
    with pytest.raises(PerformanceValidationError, match="views"):
        performance.add_snapshot(
            pub_id,
            source="manual",
            views=-1,
            likes=None,
            comments=None,
            shares=None,
        )


def test_manual_snapshot_requires_at_least_one_metric(tmp_path):
    _, publish, performance = _setup(tmp_path)
    pub_id = _published(publish)
    with pytest.raises(PerformanceValidationError, match="At least one"):
        performance.add_snapshot(
            pub_id,
            source="manual",
            views=None,
            likes=None,
            comments=None,
            shares=None,
        )


def test_derived_metrics_engagement_and_velocity():
    snapshots = [
        {
            "views": 100,
            "likes": 10,
            "comments": 2,
            "shares": 1,
            "collected_at": "2026-09-22T00:00:00+00:00",
        },
        {
            "views": 340,
            "likes": 25,
            "comments": 5,
            "shares": 4,
            "collected_at": "2026-09-22T02:00:00+00:00",
        },
    ]
    out = derived_metrics(snapshots)
    assert out["engagement_count"] == 34
    assert out["engagement_rate"] == pytest.approx(0.1)
    assert out["view_velocity_per_hour"] == pytest.approx(120.0)
    assert out["snapshot_count"] == 2


def test_feedback_summary_joins_candidate_intelligence(tmp_path):
    review, publish, performance = _setup(tmp_path)
    pub_id = _published(publish)
    performance.add_snapshot(
        pub_id,
        source="manual",
        views=1000,
        likes=80,
        comments=10,
        shares=5,
    )
    summary = build_feedback_summary(performance, review)
    assert summary["published_posts"] == 1
    assert summary["posts_with_metrics"] == 1
    assert summary["posts"][0]["features"]["topic"] == "low-cal meal"
    assert any(
        p["dimension"] == "format" and p["value"] == "recipe"
        for p in summary["patterns"]
    )
    assert summary["learning_policy"]["ranking_weights_changed"] is False


def test_multiple_snapshots_are_append_only(tmp_path):
    _, publish, performance = _setup(tmp_path)
    pub_id = _published(publish)
    performance.add_snapshot(
        pub_id,
        source="manual",
        views=100,
        likes=10,
        comments=1,
        shares=0,
    )
    performance.add_snapshot(
        pub_id,
        source="manual",
        views=200,
        likes=20,
        comments=2,
        shares=1,
    )
    rows = performance.list_snapshots(pub_id)
    assert len(rows) == 2
    assert rows[0]["views"] == 100
    assert rows[1]["views"] == 200


def test_metrics_require_published_post(tmp_path):
    _, publish, performance = _setup(tmp_path)
    pub_id = publish.create_publication(
        "douyin",
        "123",
        "facebook",
        title="T",
        caption="C",
        status="PUBLISHING",
    )
    with pytest.raises(PerformanceValidationError, match="PUBLISHED"):
        performance.add_snapshot(
            pub_id,
            source="manual",
            views=1,
            likes=None,
            comments=None,
            shares=None,
        )


def test_youtube_sync_uses_official_statistics(monkeypatch, tmp_path):
    _, publish, performance = _setup(tmp_path)
    pub_id = _published(publish, platform="youtube", external_id="video123")

    class Request:
        def execute(self):
            return {
                "items": [
                    {
                        "statistics": {
                            "viewCount": "1234",
                            "likeCount": "45",
                            "commentCount": "6",
                        }
                    }
                ]
            }

    class Videos:
        def list(self, **kwargs):
            assert kwargs["part"] == "statistics"
            assert kwargs["id"] == "video123"
            return Request()

    class YouTube:
        def videos(self):
            return Videos()

    monkeypatch.setattr(
        "viralmint_overlay.performance_service._youtube_credentials",
        lambda _: object(),
    )

    import googleapiclient.discovery

    monkeypatch.setattr(
        googleapiclient.discovery,
        "build",
        lambda *args, **kwargs: YouTube(),
    )

    snap = sync_youtube_metrics(performance, publish, pub_id)
    assert snap["source"] == "youtube_api"
    assert snap["views"] == 1234
    assert snap["likes"] == 45
    assert snap["comments"] == 6
    assert snap["shares"] is None
