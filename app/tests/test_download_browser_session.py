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
