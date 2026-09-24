from backend.services.download_browser_session import (
    SUPPORTED_DOWNLOAD_BROWSERS,
    cookiesfrombrowser_value,
    normalize_download_browser,
    normalize_download_profile,
)


def test_supported_browser_contract_is_small_and_explicit():
    assert SUPPORTED_DOWNLOAD_BROWSERS == ("auto", "edge", "chrome", "brave", "firefox")


def test_browser_normalization_defaults_to_auto_and_rejects_unknown():
    assert normalize_download_browser(None) == "auto"
    assert normalize_download_browser(" EDGE ") == "edge"
    try:
        normalize_download_browser("opera")
    except ValueError as exc:
        assert "Unsupported browser" in str(exc)
    else:
        raise AssertionError("unknown browser must be rejected")


def test_profile_normalization_and_ytdlp_tuple_shape():
    assert normalize_download_profile("  ") is None
    assert normalize_download_profile(" Profile 1 ") == "Profile 1"
    assert cookiesfrombrowser_value("edge") == ("edge",)
    assert cookiesfrombrowser_value("edge", "Profile 1") == ("edge", "Profile 1")


def test_explicit_missing_browser_is_not_reported_ready(monkeypatch):
    from backend.services import ytdlp_service as svc

    monkeypatch.setattr(svc, "_browser_known_on_machine", lambda name: False)
    assert svc._resolve_cookie_browser("edge") is None
    health = svc.browser_session_health("edge", "Default")
    assert health["status"] == "not_found"
    assert health["resolved_browser"] is None


def test_unreadable_browser_session_degrades_without_attaching_cookies(monkeypatch):
    from backend.services import ytdlp_service as svc

    monkeypatch.setattr(svc, "_resolve_cookie_browser", lambda browser: "edge")
    opts = {"http_headers": {"User-Agent": "anonymous"}}
    applied = svc._apply_cookies(
        opts, "edge", "Default", session_ok=False
    )
    assert applied is False
    assert "cookiesfrombrowser" not in opts
    assert opts["http_headers"]["User-Agent"] == "anonymous"


def test_verified_session_uses_browser_profile_without_plaintext_cookiefile(monkeypatch):
    from backend.services import ytdlp_service as svc

    monkeypatch.setattr(svc, "_resolve_cookie_browser", lambda browser: "edge")
    opts = {"http_headers": {"User-Agent": "anonymous"}}
    applied = svc._apply_cookies(
        opts, "edge", "Profile 1", session_ok=True
    )
    assert applied is True
    assert opts["cookiesfrombrowser"] == ("edge", "Profile 1")
    assert "cookiefile" not in opts
    assert opts["http_headers"]["User-Agent"] != "anonymous"


def test_cookie_drop_scope_is_youtube_only():
    from backend.services import ytdlp_service as svc

    assert svc._is_youtube_url("https://www.youtube.com/watch?v=x")
    assert svc._is_youtube_url("https://youtu.be/x")
    assert not svc._is_youtube_url("https://www.douyin.com/video/x")
    assert svc._is_bytedance_url("https://www.douyin.com/video/x")
    assert svc._is_bytedance_url("https://www.tiktok.com/@u/video/x")


def test_bytedance_cookie_count_matches_the_requested_site():
    from backend.services import ytdlp_service as svc

    session = {"douyin_cookie_count": 4, "tiktok_cookie_count": 2}
    assert svc._required_bytedance_cookie_count(
        "https://www.douyin.com/video/123", session
    ) == 4
    assert svc._required_bytedance_cookie_count(
        "https://www.tiktok.com/@u/video/123", session
    ) == 2


def test_legacy_plaintext_cookie_exports_are_removed(monkeypatch, tmp_path):
    from backend.services import ytdlp_service as svc

    monkeypatch.setattr(svc.settings, "TMP_DIR", tmp_path)
    legacy = tmp_path / "yt_cookies.txt"
    legacy.write_text("secret", encoding="utf-8")
    newer = tmp_path / "browser_cookies_edge_default.txt"
    newer.write_text("secret", encoding="utf-8")

    svc._remove_legacy_cookie_exports()

    assert not legacy.exists()
    assert not newer.exists()
