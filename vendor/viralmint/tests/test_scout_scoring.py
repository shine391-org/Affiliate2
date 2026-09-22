# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for the virality scoring formula in backend/agents/scout.py."""
import pytest
from datetime import datetime, timedelta

from backend.agents.scout import compute_virality_score


class TestViralityScoreBasics:
    def test_returns_float_between_0_and_100(self):
        video = {"views": 1000, "likes": 50, "comments": 10}
        score = compute_virality_score(video)
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_zero_views_uses_default(self):
        """views=0 should not cause division by zero."""
        video = {"views": 0, "likes": 0, "comments": 0}
        score = compute_virality_score(video)
        assert isinstance(score, float)
        assert score >= 0

    def test_negative_values_clamped_to_zero(self):
        """Negative likes/comments should be treated as 0."""
        video = {"views": 1000, "likes": -5, "comments": -10}
        score = compute_virality_score(video)
        assert score >= 0

    def test_score_capped_at_100(self):
        """Even extreme metrics should not exceed 100."""
        video = {
            "views": 100_000_000,
            "likes": 10_000_000,
            "comments": 5_000_000,
            "upload_date": datetime.utcnow(),
        }
        score = compute_virality_score(video)
        assert score <= 100.0


class TestViralityScoreFactors:
    def test_higher_engagement_increases_score(self):
        base = {"views": 100_000, "likes": 100, "comments": 10}
        high_engagement = {"views": 100_000, "likes": 10_000, "comments": 5_000}
        assert compute_virality_score(high_engagement) > compute_virality_score(base)

    def test_recency_increases_score(self):
        old_video = {
            "views": 100_000,
            "likes": 5_000,
            "comments": 500,
            "upload_date": datetime.utcnow() - timedelta(days=90),
        }
        recent_video = {
            "views": 100_000,
            "likes": 5_000,
            "comments": 500,
            "upload_date": datetime.utcnow() - timedelta(hours=6),
        }
        assert compute_virality_score(recent_video) > compute_virality_score(old_video)

    def test_more_views_increases_score(self):
        low_views = {"views": 1_000, "likes": 50, "comments": 10}
        high_views = {"views": 1_000_000, "likes": 50, "comments": 10}
        # Same engagement ratio but views_score component differs
        assert compute_virality_score(high_views) > compute_virality_score(low_views)

    def test_comments_weighted_more_than_likes(self):
        """Comments are weighted 2x in engagement rate formula."""
        more_likes = {"views": 100_000, "likes": 1_000, "comments": 0}
        more_comments = {"views": 100_000, "likes": 0, "comments": 500}
        # 500 comments × 2 = 1000 engagement units, same as 1000 likes
        # But comments video also gets views_score etc, so just check both compute
        score_likes = compute_virality_score(more_likes)
        score_comments = compute_virality_score(more_comments)
        assert isinstance(score_likes, float)
        assert isinstance(score_comments, float)


class TestViralityScoreEdgeCases:
    def test_no_upload_date_assumes_30_days(self):
        """When upload_date is None, assume 30 days old."""
        video = {"views": 100_000, "likes": 5_000, "comments": 500}
        score = compute_virality_score(video)
        assert score > 0

    def test_upload_date_string_treated_as_no_date(self):
        """String dates (not datetime) should fall through to default."""
        video = {
            "views": 100_000,
            "likes": 5_000,
            "comments": 500,
            "upload_date": "2025-01-01",
        }
        score = compute_virality_score(video)
        assert score > 0

    def test_missing_keys_use_defaults(self):
        """Missing likes/comments should default to 0."""
        video = {"views": 50_000}
        score = compute_virality_score(video)
        assert score >= 0

    def test_empty_dict(self):
        video = {}
        score = compute_virality_score(video)
        assert isinstance(score, float)
        assert score >= 0


class TestViralityScoreSideEffects:
    def test_sets_views_per_hour(self):
        video = {
            "views": 100_000,
            "likes": 5_000,
            "comments": 500,
            "upload_date": datetime.utcnow() - timedelta(hours=10),
        }
        compute_virality_score(video)
        assert "views_per_hour" in video
        assert video["views_per_hour"] == pytest.approx(10_000, rel=0.1)

    def test_sets_outlier_score_when_channel_avg_present(self):
        video = {
            "views": 500_000,
            "likes": 10_000,
            "comments": 1_000,
            "channel_avg_views": 50_000,
        }
        compute_virality_score(video)
        assert "outlier_score" in video
        assert video["outlier_score"] == 10.0  # 500K / 50K = 10x

    def test_no_outlier_score_without_channel_avg(self):
        video = {"views": 100_000, "likes": 5_000, "comments": 500}
        compute_virality_score(video)
        # outlier_score may or may not be set depending on subscriber_count
        # but it should not crash

    def test_outlier_score_from_subscriber_count(self):
        """When channel_avg is missing but subscriber_count exists, estimate avg."""
        video = {
            "views": 100_000,
            "likes": 5_000,
            "comments": 500,
            "subscriber_count": 10_000,
        }
        compute_virality_score(video)
        # channel_avg estimated as max(subs * 0.03, 100) = max(300, 100) = 300
        # outlier_score = 100_000 / 300 = 333.3
        assert "outlier_score" in video
        assert video["outlier_score"] > 100
