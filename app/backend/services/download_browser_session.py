"""Canonical browser-session selection for yt-dlp downloads.

The app stores only the browser/profile selector. Login cookies remain inside
the user's browser and are read by yt-dlp on demand.
"""
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.user_settings import UserSettings

SUPPORTED_DOWNLOAD_BROWSERS = ("auto", "edge", "chrome", "brave", "firefox")


def normalize_download_browser(value: str | None) -> str:
    browser = (value or "auto").strip().lower()
    if browser not in SUPPORTED_DOWNLOAD_BROWSERS:
        raise ValueError(f"Unsupported browser: {browser}")
    return browser


def normalize_download_profile(value: str | None) -> str | None:
    profile = (value or "").strip()
    return profile or None


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
