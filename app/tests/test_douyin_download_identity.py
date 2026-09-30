from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def test_download_metadata_preserves_verified_source_identity():
    from backend.core.task_runner import _media_metadata_with_identity

    base = {"width": 1080}
    identity = {
        "requested_aweme_id": "123",
        "candidate_aweme_id": "123",
        "provenance": "network:response-url",
    }

    combined = _media_metadata_with_identity(base, identity)

    assert combined["width"] == 1080
    assert combined["source_identity"] == identity
    assert "source_identity" not in base


class _ScalarResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        return _ScalarResult()


@pytest.mark.asyncio
async def test_exact_douyin_identity_failure_is_not_sent_to_ai_url_rewriter(monkeypatch):
    from backend.core import ai_retry, task_runner
    from backend.core.exceptions import DownloadError
    from backend import database
    from backend.services import ytdlp_service

    original = DownloadError(
        "requested aweme_id=123; observed_aweme_ids=999; refusing unverified media"
    )
    download = AsyncMock(side_effect=original)
    ai_fix = AsyncMock(return_value=None)
    monkeypatch.setattr(ytdlp_service, "download_video", download)
    monkeypatch.setattr(ai_retry, "ai_fix_url", ai_fix)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _FakeSession())

    with pytest.raises(DownloadError, match="requested aweme_id=123"):
        await task_runner._download_single_video_to_db(
            "job-1",
            "https://www.douyin.com/video/123",
            "exact video",
            "user-1",
        )

    assert download.await_count == 1
    ai_fix.assert_not_awaited()
