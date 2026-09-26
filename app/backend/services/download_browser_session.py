"""Canonical browser-session selection for yt-dlp downloads.

The app stores only the browser/profile selector. Login cookies remain inside
the user's browser and are read by yt-dlp on demand.
"""
from urllib.parse import urlparse

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.user_settings import UserSettings

SUPPORTED_DOWNLOAD_BROWSERS = ("auto", "edge", "chrome", "brave", "firefox")
DEFAULT_DOUYIN_CDP_URL = "http://127.0.0.1:9222"


def normalize_download_browser(value: str | None) -> str:
    browser = (value or "auto").strip().lower()
    if browser not in SUPPORTED_DOWNLOAD_BROWSERS:
        raise ValueError(f"Unsupported browser: {browser}")
    return browser


def normalize_download_profile(value: str | None) -> str | None:
    profile = (value or "").strip()
    return profile or None


def normalize_douyin_cdp_url(value: str | None) -> str:
    """Normalize a persisted local DevTools endpoint.

    Browser automation is intentionally local-only here. A remote CDP endpoint
    would turn a Settings field into an SSRF/browser-control surface.
    """
    raw = (value or DEFAULT_DOUYIN_CDP_URL).strip().rstrip("/")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "Douyin CDP URL must be a local http(s) endpoint such as "
            "http://127.0.0.1:9222"
        )
    if not parsed.port:
        raise ValueError("Douyin CDP URL must include the remote-debugging port")
    return raw


def cookiesfrombrowser_value(browser: str, profile: str | None = None) -> tuple:
    browser = normalize_download_browser(browser)
    if browser == "auto":
        raise ValueError("auto must be resolved to a concrete browser first")
    profile = normalize_download_profile(profile)
    return (browser, profile) if profile else (browser,)


async def get_download_browser_session() -> tuple[str, str | None]:
    """Return the saved selector, defaulting to auto / default profile."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()
    return (
        normalize_download_browser(getattr(row, "download_cookie_browser", None)),
        normalize_download_profile(getattr(row, "download_cookie_profile", None)),
    )


async def get_douyin_cdp_url() -> str:
    """Return the saved local Douyin CDP endpoint."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()
    return normalize_douyin_cdp_url(getattr(row, "douyin_cdp_url", None))
