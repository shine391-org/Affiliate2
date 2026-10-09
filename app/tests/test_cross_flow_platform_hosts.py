"""Platform routing must depend on the actual host, never a path or mobile prefix."""
import pytest

from backend.core.task_runner import _detect_platform
from backend.services import ytdlp_service as yt


@pytest.mark.parametrize("url,platform", [
    ("https://m.tiktok.com/@user/video/123", "tiktok"),
    ("https://vm.tiktok.com/short", "tiktok"),
    ("https://www.tiktok.com/@user/video/123", "tiktok"),
    ("https://m.youtube.com/watch?v=123", "youtube"),
    ("https://youtu.be/123", "youtube"),
    ("https://v.douyin.com/short", "douyin"),
    ("https://m.iesdouyin.com/share/video/123", "douyin"),
    ("https://m.bilibili.com/video/BV123", "bilibili"),
    ("https://news.example.com/tiktok.com/video", "example"),
])
def test_platform_detects_hostname_without_confusing_mobile_prefix(url, platform):
    assert _detect_platform(url) == platform


@pytest.mark.parametrize("url", [
    "https://example.com/tiktok.com/video",
    "https://example.com/redirect?next=https://www.douyin.com/video/123",
    "https://notyoutube.com/youtube.com/watch",
    "https://tiktok.com.evil.example.net/watch",
])
def test_unrelated_host_never_selects_authenticated_provider(url):
    assert not yt._is_tiktok_url(url)
    assert not yt._is_douyin_url(url)
    assert not yt._is_youtube_url(url)
    assert yt._required_bytedance_cookie_count(
        url, {"douyin_cookie_count": 5, "tiktok_cookie_count": 5}
    ) == 0


def test_real_mobile_provider_hosts_retain_cookie_routing():
    session = {"douyin_cookie_count": 7, "tiktok_cookie_count": 3}
    assert yt._is_tiktok_url("https://vm.tiktok.com/foo")
    assert yt._is_douyin_url("https://v.douyin.com/bar")
    assert yt._required_bytedance_cookie_count(
        "https://vm.tiktok.com/foo", session
    ) == 3
    assert yt._required_bytedance_cookie_count(
        "https://v.douyin.com/bar", session
    ) == 7
