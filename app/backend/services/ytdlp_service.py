# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""yt-dlp download wrapper with error handling and rate-limit resilience."""
import asyncio
import glob
import logging
import os
import platform
import re
import shutil
import time
from pathlib import Path
from backend.config import settings
from backend.core.exceptions import DownloadError, VideoUnavailableError, RateLimitError
from backend.core.http_utils import get_user_agent
from backend.services.download_browser_session import (
    cookiesfrombrowser_value,
    get_download_browser_session,
    normalize_download_browser,
    normalize_download_profile,
)

logger = logging.getLogger(__name__)


def _yt_dlp_http_headers(*, authenticated: bool = False) -> dict:
    """Headers for yt-dlp requests.

    Anonymous traffic keeps the existing rotating browser-like UA. When browser
    cookies are attached we deliberately stop rotating the UA: a random UA
    paired with a logged-in browser session is a needless anti-bot mismatch.
    """
    if authenticated:
        try:
            from yt_dlp.utils import std_headers
            user_agent = std_headers.get("User-Agent") or get_user_agent()
        except Exception:
            user_agent = get_user_agent()
    else:
        user_agent = get_user_agent()
    return {
        "User-Agent": user_agent,
        "Referer": "https://www.google.com/",
    }


class _YtDlpLogger:
    """Redirect yt-dlp output to Python logging instead of stdout/stderr.
    Prevents Broken pipe (EPIPE) when stdout is closed (e.g. piped through head)."""
    def debug(self, msg):
        if msg.startswith('[download]'):
            logger.debug(msg)
        else:
            logger.debug(msg)

    def info(self, msg):
        logger.info(msg)

    def warning(self, msg):
        logger.warning(msg)

    def error(self, msg):
        logger.error(msg)


# curl-cffi browser impersonation — replays Chrome's TLS handshake (JA3
# fingerprint) so sites with TLS-aware bot defenses (Cloudflare/Akamai)
# can't fingerprint the client by handshake alone. Wrapped in try/except so
# a missing or version-incompatible curl-cffi NEVER breaks downloads —
# they just fall back to urllib's default fingerprint.
#
# Requires curl-cffi >= 0.15 (see the pin note in requirements.txt): 0.14's
# streaming path resets a libcurl handle from a done-callback while the
# calling thread is still reading it, which SIGABRTs the whole backend
# process. TikTok's extractor forces impersonate=True, so that is every
# TikTok probe and download.
_IMPERSONATE_TARGET = None
try:
    import curl_cffi  # noqa: F401  (presence test; yt-dlp loads its own handler)
    from yt_dlp.networking.impersonate import ImpersonateTarget as _ImpersonateTarget
    _IMPERSONATE_TARGET = _ImpersonateTarget("chrome")
except Exception as _e:  # noqa: BLE001
    logger.info("curl-cffi impersonation unavailable: %s — downloads use urllib fingerprint", _e)


def _yt_dlp_base_opts() -> dict:
    """Base options for all yt-dlp calls — quiet mode + logger redirect.

    `format_sort` declares preference order for the picked format. `lang` is
    FIRST so that on YouTube multi-language videos we pick the ORIGINAL audio
    track — without it, plain `bestaudio` sorted by bitrate alone, which on
    multi-language videos commonly picked a dub (observed: an English video
    downloading with Portuguese audio). No-op for non-YouTube sources.

    `retry_sleep_functions` aligns with yt-dlp's documented best practice for
    resilience under rate limits: exponential backoff per retry-pool instead
    of a static sleep that wastes retry budget.

    `throttledratelimit` catches stale signed URLs that "succeed" but trickle
    bytes — yt-dlp re-extracts from scratch when speed drops below it.

    `impersonate` enables curl-cffi browser fingerprinting when the lib is
    importable; otherwise omitted (no-op fallback to urllib).
    """
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YtDlpLogger(),
        "format_sort": ["lang", "res:1080", "fps", "codec:avc:m4a", "size", "br"],
        "retry_sleep_functions": {
            "http":        lambda n: min(60, 2 ** n),         # 1, 2, 4, 8, 16, 32, 60...
            "fragment":    lambda n: min(120, 2 * (n + 1)),   # 2, 4, 6, 8... (linear, capped)
            "file_access": lambda n: 0.5 * n,                 # quick disk retries
        },
        "throttledratelimit": 100_000,  # 100 KB/s
    }
    if _IMPERSONATE_TARGET is not None:
        opts["impersonate"] = _IMPERSONATE_TARGET
    return opts


# JS-runtime discovery cache. A positive result is process-lifetime (a runtime
# doesn't move once found); a NEGATIVE result is deliberately not cached, so a
# user who installs Node or deno mid-session benefits on the very next
# download. The cost is two shutil.which calls — noise next to a yt-dlp run.
_JS_RUNTIMES_RESOLVED: dict | None = None
_warned_no_js_runtime = False


def _resolve_js_runtimes() -> dict:
    """Build yt-dlp's `js_runtimes` dict from what this machine actually has.

    Why discovery instead of a static pin: this used to be hardcoded
    `{"node": {}}`, which carried two silent failure modes on a machine
    without Node (the common case for packaged desktop users — the app bundles
    no JS runtime):

      1. Explicitly passing js_runtimes REPLACES yt-dlp's default, so the
         hardcoded pin also disabled the `deno` default — a user with deno but
         not node got NO runtime instead of one.
      2. With no runtime the EJS challenge solver can't run, n-signature
         challenges go unsolved, and YouTube formats go missing or 403 —
         indistinguishable in a bug report from a cookie problem.

    Nothing found → pass the default-shaped {'deno': {}} through and log ONE
    clear warning naming the consequence, instead of letting yt-dlp's buried
    per-download warning be the only signal.
    """
    global _JS_RUNTIMES_RESOLVED, _warned_no_js_runtime
    if _JS_RUNTIMES_RESOLVED is not None:
        return dict(_JS_RUNTIMES_RESOLVED)

    runtimes: dict = {}
    if shutil.which("deno"):
        runtimes["deno"] = {}
    if shutil.which("node"):
        runtimes["node"] = {}

    if runtimes:
        _JS_RUNTIMES_RESOLVED = dict(runtimes)
        return dict(runtimes)

    # Nothing found. Don't cache — the user may install a runtime later.
    if not _warned_no_js_runtime:
        _warned_no_js_runtime = True
        logger.warning(
            "No JavaScript runtime (deno/node) found — YouTube n-signature "
            "challenges can't be solved, so some formats will be missing or "
            "fail with HTTP 403. Installing Node.js or deno fixes this."
        )
    return {"deno": {}}


def _yt_dlp_js_opts() -> dict:
    """Enable a JS runtime + EJS challenge solver for YouTube extraction,
    plus the per-extractor tweaks that keep the flaky sites working.

    Runtime selection is machine-aware — see `_resolve_js_runtimes`.

    - **youtube**: cycle player clients, leading with the three that yt-dlp's
      PO Token Guide lists as NOT requiring a token (tv, web_embedded,
      android_vr) — when one client gets bot-blocked, the next takes over.
      `webpage_client` controls the identity for the initial page scrape,
      distinct from the streaming-URL request. `formats: missing_pot` allows
      degraded-quality formats over no download at all.
    - **youtubetab**: `skip=authcheck` — with cookies present, public channel
      extraction otherwise fails an unnecessary auth check.
    - **tiktok**: `app_info=[""]` triggers the genuine-device-id flow (most
      stable); app versions match TikTok's current mobile app.
    - **twitter**: force the syndication API — the default graphql path 403s
      guest-token requests for most tweets.
    - **instagram/reddit**: bump extractor retries (known to flake).
    """
    return {
        "js_runtimes": _resolve_js_runtimes(),
        "remote_components": {"ejs:github": {}},
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "tv", "web_embedded", "android_vr",   # no-token (lead)
                    "tv_simply", "ios", "mweb",            # token-gated fallbacks
                    "web_safari", "web",                    # last resort
                ],
                "webpage_client": ["web"],
                "formats": ["missing_pot"],
            },
            "youtubetab": {"skip": ["authcheck"]},
            "tiktok": {
                "app_info": [""],
                "app_version": ["35.1.3"],
                "manifest_app_version": ["2023501030"],
            },
            "twitter": {"api": ["syndication"]},
            "instagram": {"extractor_retries": ["5"]},
            "reddit": {"extractor_retries": ["5"]},
        },
    }


def _get_ytdlp_age_days() -> int:
    """Return how many days old the installed yt-dlp version is, or -1 if unknown."""
    import importlib.metadata
    from datetime import datetime
    try:
        version = importlib.metadata.version("yt-dlp")
        date_part = ".".join(version.split(".")[:3])
        release_date = datetime.strptime(date_part, "%Y.%m.%d")
        return (datetime.now() - release_date).days
    except Exception:
        return -1


# Track consecutive failures to trigger auto-update
_consecutive_download_failures = 0
_FAILURE_THRESHOLD_FOR_UPDATE = 3  # Auto-update after 3 consecutive failures
_last_update_attempt = 0.0
_UPDATE_COOLDOWN = 3600  # Don't try updating more than once per hour


def check_ytdlp_version():
    """Log a warning if yt-dlp is more than 30 days old. Auto-update if stale."""
    import importlib.metadata
    from datetime import datetime

    try:
        version = importlib.metadata.version("yt-dlp")
        age_days = _get_ytdlp_age_days()

        if age_days > 30:
            logger.warning(
                "yt-dlp version %s is %d days old. Attempting auto-update...",
                version, age_days,
            )
            _try_update_ytdlp()
        else:
            logger.info("yt-dlp version %s (%d days old)", version, age_days)
    except Exception as e:
        logger.warning("Could not determine yt-dlp version: %s", e)


def _try_update_ytdlp() -> bool:
    """Attempt to update yt-dlp via pip. Returns True if updated successfully."""
    global _last_update_attempt
    import subprocess
    import sys

    # In a frozen PyInstaller bundle, sys.executable points at our own
    # bundled binary, not Python — running `sys.executable -m pip install -U`
    # would re-invoke desktop_app.main() and fork-bomb the launcher. There's
    # no pip in the bundle anyway. Skip the update; users should re-download
    # the installer when yt-dlp gets stale.
    if getattr(sys, "frozen", False):
        logger.info("Skipping yt-dlp auto-update in frozen build — re-install ViralMint to refresh")
        return False

    now = time.time()
    if now - _last_update_attempt < _UPDATE_COOLDOWN:
        logger.debug("Skipping yt-dlp update — last attempt was %ds ago", int(now - _last_update_attempt))
        return False
    _last_update_attempt = now

    try:
        logger.info("Auto-updating yt-dlp...")
        # Version-bounded spec: on an environment whose Python is too old for
        # current yt-dlp, an unbounded "-U yt-dlp" silently resolves to an
        # ANCIENT release (macOS system python3 = 3.9 → a 2025.10 build that
        # fails on modern YouTube). Bounded, pip errors loudly instead of
        # installing a silently-broken downloader.
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp>=2026.7.4"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            # Reload the yt_dlp module to pick up the new version
            import importlib
            import yt_dlp
            importlib.reload(yt_dlp)
            new_age = _get_ytdlp_age_days()
            logger.info("yt-dlp updated successfully (now %d days old)", new_age)
            return True
        else:
            logger.warning("yt-dlp update failed: %s", result.stderr[:200])
            return False
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp update timed out")
        return False
    except Exception as e:
        logger.warning("yt-dlp update error: %s", e)
        return False


def _record_download_failure():
    """Track consecutive failures; trigger yt-dlp update after threshold."""
    global _consecutive_download_failures
    _consecutive_download_failures += 1
    if _consecutive_download_failures >= _FAILURE_THRESHOLD_FOR_UPDATE:
        age = _get_ytdlp_age_days()
        if age > 7:  # Only auto-update if at least a week old
            logger.warning(
                "%d consecutive download failures with yt-dlp %d days old — triggering auto-update",
                _consecutive_download_failures, age,
            )
            if _try_update_ytdlp():
                _consecutive_download_failures = 0  # Reset on successful update

# ── Rate-limit tracking ──────────────────────────────────────────────────────
# YouTube rate-limits at the IP level after heavy download volume.
# Track consecutive rate-limit hits and apply exponential backoff.
_rate_limit_state = {
    "consecutive_429s": 0,       # how many 429s in a row
    "last_429_time": 0.0,        # timestamp of last 429
    "cooldown_until": 0.0,       # don't attempt downloads until this time
}

RATE_LIMIT_BACKOFF_BASE = 60     # seconds — first backoff
RATE_LIMIT_BACKOFF_MAX = 300     # seconds — max backoff (5 min)
RATE_LIMIT_RESET_AFTER = 600    # seconds — reset counter after 10 min of no 429s


def _record_rate_limit():
    """Record a rate-limit hit and compute next cooldown."""
    now = time.time()
    # Reset counter if it's been a while since last 429
    if now - _rate_limit_state["last_429_time"] > RATE_LIMIT_RESET_AFTER:
        _rate_limit_state["consecutive_429s"] = 0

    _rate_limit_state["consecutive_429s"] += 1
    _rate_limit_state["last_429_time"] = now

    backoff = min(
        RATE_LIMIT_BACKOFF_BASE * (2 ** (_rate_limit_state["consecutive_429s"] - 1)),
        RATE_LIMIT_BACKOFF_MAX,
    )
    _rate_limit_state["cooldown_until"] = now + backoff
    logger.warning(
        "Rate limit hit #%d — backing off %ds (until %s)",
        _rate_limit_state["consecutive_429s"],
        backoff,
        time.strftime("%H:%M:%S", time.localtime(now + backoff)),
    )
    return backoff


def _record_success():
    """Record a successful download — reset consecutive 429 counter."""
    _rate_limit_state["consecutive_429s"] = 0


def _check_cooldown() -> float:
    """Return seconds to wait before next download, or 0 if ready."""
    remaining = _rate_limit_state["cooldown_until"] - time.time()
    return max(remaining, 0.0)

# ── Browser-session cookies ─────────────────────────────────────────────────
# The canonical downloader stores only browser/profile selectors. Cookie
# values remain in the browser; unlike the previous implementation we do NOT
# export an all-sites Netscape cookie jar to disk.

_BROWSER_COOKIE_SOURCE = None
_SUPPORTED_BROWSER_BINARIES = {
    "chrome": ("chrome", "google-chrome"),
    "brave": ("brave", "brave-browser"),
    "edge": ("msedge", "microsoft-edge", "edge"),
    "firefox": ("firefox",),
}


def _browser_known_on_machine(name: str) -> bool:
    """Best-effort browser availability check, including normal Windows paths."""
    for binary in _SUPPORTED_BROWSER_BINARIES.get(name, (name,)):
        if shutil.which(binary):
            return True
    if platform.system() != "Windows":
        return False
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    suffixes = {
        "chrome": ("Google/Chrome/Application/chrome.exe",),
        "brave": ("BraveSoftware/Brave-Browser/Application/brave.exe",),
        "edge": ("Microsoft/Edge/Application/msedge.exe",),
        "firefox": ("Mozilla Firefox/firefox.exe",),
    }
    return any(
        root and (Path(root) / suffix).exists()
        for root in roots
        for suffix in suffixes.get(name, ())
    )


def _detect_cookie_browser() -> str | None:
    """Auto-detect once, preferring Edge on Windows for this desktop workflow."""
    global _BROWSER_COOKIE_SOURCE
    if _BROWSER_COOKIE_SOURCE is not None:
        return _BROWSER_COOKIE_SOURCE or None
    for name in ("chrome", "brave", "edge", "firefox"):
        if _browser_known_on_machine(name):
            _BROWSER_COOKIE_SOURCE = name
            logger.info("Auto-detected browser for download cookies: %s", name)
            return name
    _BROWSER_COOKIE_SOURCE = ""
    return None


def _resolve_cookie_browser(browser: str | None) -> str | None:
    selected = normalize_download_browser(browser)
    if selected == "auto":
        return _detect_cookie_browser()
    return selected if _browser_known_on_machine(selected) else None


def _cookie_domain_counts(cookie_jar) -> dict:
    counts = {"total": 0, "douyin": 0, "tiktok": 0}
    for cookie in cookie_jar or ():
        domain = (getattr(cookie, "domain", "") or "").lower()
        counts["total"] += 1
        if "douyin.com" in domain:
            counts["douyin"] += 1
        if "tiktok.com" in domain:
            counts["tiktok"] += 1
    return counts


def _remove_legacy_cookie_exports() -> None:
    """Remove cookie jars written by older downloader revisions.

    Those files contained cookies for every site in the browser and are no
    longer part of the canonical design.
    """
    candidates = [settings.TMP_DIR / "yt_cookies.txt"]
    candidates.extend(settings.TMP_DIR.glob("browser_cookies_*.txt"))
    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove legacy cookie export: %s", path)


def _inspect_browser_session(browser: str = "auto", profile: str | None = None) -> dict:
    """Read browser cookies locally with yt-dlp; no network request is made."""
    _remove_legacy_cookie_exports()
    selected = normalize_download_browser(browser)
    profile = normalize_download_profile(profile)
    concrete = _resolve_cookie_browser(selected)
    if not concrete:
        return {
            "ok": False,
            "browser": selected,
            "resolved_browser": None,
            "profile": profile,
            "error": "Selected browser was not found on this machine.",
            "cookie_count": 0,
            "douyin_cookie_count": 0,
            "tiktok_cookie_count": 0,
        }
    try:
        import yt_dlp
        from yt_dlp.cookies import load_cookies
        with yt_dlp.YoutubeDL({**_yt_dlp_base_opts(), "skip_download": True}) as ydl:
            jar = load_cookies(
                None,
                cookiesfrombrowser_value(concrete, profile),
                ydl,
            )
        counts = _cookie_domain_counts(jar)
        return {
            "ok": True,
            "browser": selected,
            "resolved_browser": concrete,
            "profile": profile,
            "cookie_count": counts["total"],
            "douyin_cookie_count": counts["douyin"],
            "tiktok_cookie_count": counts["tiktok"],
        }
    except Exception as exc:
        logger.warning("Could not read %s browser cookies: %s", concrete, exc)
        return {
            "ok": False,
            "browser": selected,
            "resolved_browser": concrete,
            "profile": profile,
            "error": (
                "Could not read cookies from this browser/profile. "
                "On Windows, close the browser once and retry; Chromium cookie "
                "encryption can also prevent yt-dlp from decrypting a session."
            ),
            "detail": str(exc)[:300],
            "cookie_count": 0,
            "douyin_cookie_count": 0,
            "tiktok_cookie_count": 0,
        }


def _apply_cookies(
    opts: dict,
    browser: str | None = "auto",
    profile: str | None = None,
    *,
    session_ok: bool | None = None,
) -> bool:
    """Attach the configured browser session only when it is known readable.

    Returning False means the caller should continue anonymously. This prevents
    a broken/locked Edge cookie DB from breaking public downloads.
    """
    concrete = _resolve_cookie_browser(browser)
    profile = normalize_download_profile(profile)
    if not concrete:
        return False
    if session_ok is None:
        session_ok = _inspect_browser_session(concrete, profile)["ok"]
    if not session_ok:
        return False
    opts["cookiesfrombrowser"] = cookiesfrombrowser_value(concrete, profile)
    opts["http_headers"] = _yt_dlp_http_headers(authenticated=True)
    return True


def _is_youtube_url(url: str) -> bool:
    value = (url or "").lower()
    return "youtube.com/" in value or "youtu.be/" in value


def _is_bytedance_url(url: str) -> bool:
    value = (url or "").lower()
    return "douyin.com/" in value or "iesdouyin.com/" in value or "tiktok.com/" in value


def _required_bytedance_cookie_count(url: str, session: dict) -> int:
    value = (url or "").lower()
    if "douyin.com/" in value or "iesdouyin.com/" in value:
        return int(session.get("douyin_cookie_count") or 0)
    if "tiktok.com/" in value:
        return int(session.get("tiktok_cookie_count") or 0)
    return 0


async def probe_browser_session(browser: str = "auto", profile: str | None = None) -> dict:
    """Verify local cookie readability; this does not claim site login validity."""
    return await asyncio.to_thread(_inspect_browser_session, browser, profile)


def browser_session_health(browser: str = "auto", profile: str | None = None) -> dict:
    selected = normalize_download_browser(browser)
    profile = normalize_download_profile(profile)
    concrete = _resolve_cookie_browser(selected)
    if not concrete:
        return {
            "status": "not_found",
            "browser": selected,
            "resolved_browser": None,
            "profile": profile,
        }
    return {
        "status": "configured" if selected != "auto" else "auto",
        "browser": selected,
        "resolved_browser": concrete,
        "profile": profile,
    }



def _drop_cookies_for_403(opts: dict) -> bool:
    """Remove the cookie jar from `opts` after an HTTP 403. True if it changed.

    This is NOT a give-up step. On YouTube, supplying ANY cookie makes yt-dlp
    skip every player client that can't carry one — `android_vr`, `tv_simply`,
    `ios` (visible in logs as `Skipping client "android_vr" since it does not
    support cookies`). Those are exactly the clients yt-dlp's PO-Token guide
    lists as token-free, so what remains hands back stream URLs that need an
    n-signature solve; without a JS runtime that can't happen and googlevideo
    answers 403. Dropping the jar puts the token-free clients back in play.
    """
    had = "cookiefile" in opts or "cookiesfrombrowser" in opts
    opts.pop("cookiefile", None)
    opts.pop("cookiesfrombrowser", None)
    return had


def _is_bot_detection_error(error_str: str) -> bool:
    """Check if the error is a YouTube bot detection / sign-in required error."""
    indicators = ["sign in to confirm", "not a bot", "cookies-from-browser", "login required"]
    return any(ind in error_str.lower() for ind in indicators)


def _is_transient_error(error_str: str) -> bool:
    """Check if the error is transient (worth retrying after a delay)."""
    indicators = [
        "broken pipe", "connection reset", "errno 32", "errno 104",
        "incomplete read", "connection aborted", "connection refused",
        "timed out", "timeout", "temporary failure", "name resolution",
        "ssl", "certificate", "eof occurred", "server error",
        "503", "502", "500", "urlopen error", "http error 5",
        "network is unreachable", "no route to host",
        "connection timed out", "read timed out",
    ]
    return any(ind in error_str.lower() for ind in indicators)


def _is_permanent_error(error_str: str) -> bool:
    """Check if the error is permanent (no point retrying)."""
    indicators = [
        "private", "unavailable", "removed", "deleted", "not found",
        "copyright", "terminated", "blocked", "geo", "age",
        "members only", "this video is not available",
    ]
    return any(ind in error_str.lower() for ind in indicators)


# Format fallback chain — progressively simpler format selections.
# Some videos have limited formats (e.g. live streams, premieres, shorts).
FORMAT_FALLBACK_CHAIN = [
    # Quality-first: preserve the best eligible source before trying any
    # compatibility fallback. Phase C deliberately removes the old 720p-first
    # default because every downstream encode starts from these pixels.
    "bestvideo+bestaudio/best",
    "best",
    # Recovery only: rare extractor/merge edge cases can reject the
    # unconstrained selector while a conventional bounded stream still works.
    "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "bestvideo[height<=720]+bestaudio/best[height<=720]",
]


def _find_actual_video_file(output_dir: Path, filename_stem: str) -> Path | None:
    """Find the actual downloaded video file — yt-dlp may change the extension after merging."""
    video_exts = [".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv"]
    for ext in video_exts:
        candidate = output_dir / f"{filename_stem}{ext}"
        if candidate.exists():
            return candidate
    # Fallback: glob for any file matching the stem
    matches = list(output_dir.glob(f"{filename_stem}.*"))
    # Exclude subtitle files
    matches = [m for m in matches if m.suffix.lower() not in (".srt", ".vtt", ".ass", ".json", ".txt")]
    if matches:
        # Return the largest file (most likely the video)
        return max(matches, key=lambda p: p.stat().st_size)
    return None


def _extract_audio_locally(video_path: Path, audio_dir: Path, filename_stem: str) -> Path | None:
    """Extract audio from a local video file using FFmpeg instead of re-downloading."""
    import subprocess
    audio_path = audio_dir / f"{filename_stem}_audio.mp3"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame",
             "-ab", "192k", "-ar", "44100", str(audio_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
        logger.warning(f"FFmpeg audio extraction returned {result.returncode}: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        logger.warning(f"FFmpeg audio extraction timed out for {video_path}")
    except FileNotFoundError:
        logger.warning("FFmpeg not found — cannot extract audio locally")
    return None


def _cleanup_subtitle_files(output_dir: Path, filename_stem: str):
    """Remove downloaded subtitle files after they've been parsed."""
    for ext in ("srt", "vtt"):
        for f in output_dir.glob(f"{filename_stem}.*.{ext}"):
            try:
                f.unlink()
            except Exception:
                pass


def _cleanup_partial_files(output_dir: Path, filename_stem: str):
    """Remove leftover .part files from failed/interrupted downloads."""
    cleaned = 0
    for f in output_dir.glob(f"{filename_stem}*.part"):
        try:
            f.unlink()
            cleaned += 1
        except Exception:
            pass
    if cleaned:
        logger.info(f"Cleaned up {cleaned} partial file(s) for {filename_stem}")


async def download_video(
    url: str,
    output_dir: Path = None,
    filename: str = None,
    extract_audio: bool = True,
    options: dict | None = None,
) -> dict:
    """
    Download a video using yt-dlp.
    Returns: {"video_path": Path, "audio_path": Path|None, "duration": int, "file_size_mb": float,
              "subtitles": list|None, "chapters": list|None, "tags": list|None, "category": str|None,
              "height": int|None, "media_metadata": dict, "requested_quality": str|None, "kept_subtitle_paths": list,
              "option_postprocessors_dropped": bool}
    Raises: RateLimitError, VideoUnavailableError, DownloadError

    `options`: per-download yt-dlp options from
    `backend.services.download_options` (quality / subtitles / container /
    thumbnail / metadata). **None or {} means the pre-existing default path,
    byte-identical to before this parameter existed** — the module-level
    FORMAT_FALLBACK_CHAIN, the standard sidecar-subtitle sweep, no
    postprocessors. Options are strictly additive; see that module for why a
    quality cap degrades instead of failing.

    `height` in the return is the resolution actually delivered, which is NOT
    always the one requested (a cap falls through to the uncapped rungs rather
    than erroring). Surfacing it is what lets a UI say "you asked for 4K, this
    source only had 1080p" instead of implying the cap was honoured.
    """
    if output_dir is None:
        output_dir = settings.VIDEOS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    file_stem = filename or ""

    # Per-download options. Normalized ONCE here so every branch below (base
    # opts, the format ladder, the subtitle sweep) reads the same cleaned
    # dict, and so a malformed dict from an LLM degrades to {} = default path
    # rather than raising mid-download.
    from backend.services import download_options as _dlopts
    _opts = _dlopts.normalize(options)
    _opt_overrides = _dlopts.ydl_overrides(_opts)
    _opt_postprocessors = _opt_overrides.pop("_vm_postprocessors", [])
    _format_chain = _dlopts.format_chain(_opts) or FORMAT_FALLBACK_CHAIN
    cookie_browser, cookie_profile = await get_download_browser_session()
    cookie_session = await asyncio.to_thread(
        _inspect_browser_session, cookie_browser, cookie_profile
    )
    cookie_session_ok = bool(cookie_session.get("ok"))
    if _is_bytedance_url(url):
        if not cookie_session_ok:
            raise DownloadError(
                "Douyin/TikTok needs a readable logged-in browser session. "
                "Open Settings > Source Providers, select the browser/profile "
                "you actually use, test it, then retry. "
                f"({cookie_session.get('error') or 'browser session unavailable'})"
            )
        if _required_bytedance_cookie_count(url, cookie_session) <= 0:
            raise DownloadError(
                "The selected browser/profile is readable but has no matching "
                "Douyin/TikTok cookies. Log in to that site in the same profile, "
                "refresh it, then retry."
            )

    # Wait out any active rate-limit cooldown before starting
    cooldown = _check_cooldown()
    if cooldown > 0:
        logger.info(f"Rate-limit cooldown active — waiting {cooldown:.0f}s before downloading {url[:60]}")
        await asyncio.sleep(cooldown)

    def _download():
        import yt_dlp

        video_path = None
        audio_path = None

        # Base video download options (format set per-attempt via fallback chain)
        base_opts = {
            **_yt_dlp_base_opts(),
            **_yt_dlp_js_opts(),
            "outtmpl": str(output_dir / (filename or "%(id)s")) + ".%(ext)s",
            "extract_flat": False,
            "socket_timeout": 30,
            "retries": 5,
            "fragment_retries": 5,
            "file_access_retries": 3,
            "extractor_retries": 3,
            # Anti-detection: sleep between yt-dlp internal requests
            "sleep_interval": 3,
            "max_sleep_interval": 8,
            "sleep_interval_requests": 1,
            # Browser-like headers
            "http_headers": _yt_dlp_http_headers(),
            # Subtitle download
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "zh", "ja", "ko", "es", "fr", "de", "pt", "ru", "ar"],
            "subtitlesformat": "srt/vtt/best",
        }

        # Use cached browser cookies (avoids repeated Keychain prompts on macOS)
        _apply_cookies(
            base_opts, cookie_browser, cookie_profile, session_ok=cookie_session_ok
        )

        # Per-download options LAST so an explicit request wins over the
        # standard layer (e.g. narrowing subtitleslangs from the 10-language
        # transcript sweep to the one language the user asked to embed).
        # Postprocessors are APPENDED, never assigned — so a future caller
        # that installs its own on this dict keeps them.
        if _opt_overrides:
            base_opts.update(_opt_overrides)
        if _opt_postprocessors:
            base_opts["postprocessors"] = [
                *(base_opts.get("postprocessors") or []),
                *_opt_postprocessors,
            ]

        def _attempt_download(opts):
            """Single download attempt — raises on failure."""
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return Path(ydl.prepare_filename(info)), info

        def _classify_and_raise(error):
            """Classify a yt-dlp error and raise the appropriate exception type."""
            error_str = str(error).lower()
            _cleanup_partial_files(output_dir, file_stem)
            if _is_permanent_error(error_str):
                raise VideoUnavailableError(f"Video unavailable: {error}")
            if "429" in error_str or "rate" in error_str:
                _record_rate_limit()
                raise RateLimitError(
                    f"The source site is rate-limiting downloads from this IP. "
                    f"Try again in a few minutes. (Error: {error})"
                )
            _record_download_failure()
            raise DownloadError(f"Download failed: {error}")

        # ── Main download with multi-layer retry ────────────────────────────
        last_error = None
        # Whether the per-download optional postprocessors (embed thumbnail /
        # subtitles / metadata from `options`) were dropped after a
        # postprocessing failure. Lives OUTSIDE the format loop: once an extra
        # proved un-embeddable for this source, re-attaching it on the next
        # format rung would just fail the same way.
        opt_pps_dropped = False

        # `_format_chain` is FORMAT_FALLBACK_CHAIN unless the caller asked for
        # a specific quality — see download_options.format_chain() on why an
        # unrequested download must keep the module-level chain verbatim.
        for fmt_idx, fmt in enumerate(_format_chain):
            video_opts = {**base_opts, "format": fmt}

            # Up to 2 attempts per format (original + 1 retry for transient errors)
            for attempt in range(2):
                try:
                    video_path, info = _attempt_download(video_opts)
                    duration = info.get("duration", 0)
                    _record_success()
                    global _consecutive_download_failures
                    _consecutive_download_failures = 0
                    last_error = None
                    break  # Success — exit retry loop

                except yt_dlp.utils.DownloadError as e:
                    last_error = e
                    error_str = str(e).lower()

                    # ── Permanent errors: don't retry, raise immediately ────
                    if _is_permanent_error(error_str):
                        _cleanup_partial_files(output_dir, file_stem)
                        raise VideoUnavailableError(f"Video unavailable: {e}")

                    if "429" in error_str or "rate" in error_str:
                        # Subtitle-specific 429: retry without subtitles, don't give up
                        if ("subtitle" in error_str or "subtitles" in error_str):
                            logger.warning(f"Subtitle download rate-limited, retrying without subtitles")
                            _cleanup_partial_files(output_dir, file_stem)
                            video_opts["writesubtitles"] = False
                            video_opts["writeautomaticsub"] = False
                            continue  # Retry same format without subtitles

                        # Full 429 rate limit — no point retrying
                        _cleanup_partial_files(output_dir, file_stem)
                        _record_rate_limit()
                        raise RateLimitError(
                            f"The source site is rate-limiting downloads from this IP. "
                            f"Try again in a few minutes. (Error: {e})"
                        )

                    # ── HTTP 403 with cookies applied: retry WITHOUT them ────
                    # A 403 is often caused BY the cookie jar (see
                    # _drop_cookies_for_403). Retrying the same configuration
                    # can only fail the same way, so drop the jar once and try
                    # again — including across the format fallbacks, hence the
                    # mirror onto base_opts, which every later format is
                    # rebuilt from.
                    if _is_youtube_url(url) \
                            and ("http error 403" in error_str or "403: forbidden" in error_str) \
                            and _drop_cookies_for_403(video_opts):
                        for _ck in ("cookiefile", "cookiesfrombrowser"):
                            base_opts.pop(_ck, None)
                        logger.warning(
                            "HTTP 403 with cookies applied — retrying without them "
                            "(re-enables the token-free player clients)"
                        )
                        _cleanup_partial_files(output_dir, file_stem)
                        continue

                    # ── Bot detection: retry with fresh cookies ────
                    if _is_bot_detection_error(str(e)) and attempt == 0:
                        browser = _resolve_cookie_browser(cookie_browser)
                        if browser:
                            logger.warning(
                                "Bot detection triggered, retrying with configured %s session",
                                browser,
                            )
                            _cleanup_partial_files(output_dir, file_stem)
                            _apply_cookies(
                                video_opts, cookie_browser, cookie_profile,
                                session_ok=cookie_session_ok,
                            )
                            import time as _time
                            _time.sleep(3)
                            continue  # Retry with the same configured session
                        else:
                            _cleanup_partial_files(output_dir, file_stem)
                            raise DownloadError(
                                "This site requires a logged-in browser session. "
                                "Configure Settings > Source Providers > Download Browser Session, then retry."
                            )

                    # ── Transient errors: retry once after delay ────
                    if _is_transient_error(error_str) and attempt == 0:
                        logger.warning(f"Transient error (attempt {attempt + 1}), retrying in 5s: {e}")
                        _cleanup_partial_files(output_dir, file_stem)
                        import time as _time
                        _time.sleep(5)
                        continue  # Retry

                    # ── Format-related errors: try next format ────
                    if any(s in error_str for s in ["no video formats", "requested format", "format is not available", "merge"]):
                        logger.warning(f"Format '{fmt}' failed, trying next fallback: {e}")
                        _cleanup_partial_files(output_dir, file_stem)
                        break  # Break retry loop, try next format

                    # ── Optional-postprocessor failure: drop extras, keep video ──
                    # Postprocessor errors fire AFTER the video is fully on
                    # disk — failing the download over cover art would break
                    # the options contract (degrade, never fail; see
                    # download_options). Known offender: EmbedThumbnail
                    # hard-raises on webm (single-stream downloads dodge the
                    # merge_output_format steer). Drop OUR appended
                    # postprocessors and retry the same format — yt-dlp sees
                    # the finished file and skips straight to postprocessing,
                    # so the retry costs no bandwidth. Runs AFTER the
                    # format/merge predicate on purpose: yt-dlp's merger is
                    # also a postprocessor, and a merge failure must keep its
                    # walk down the format chain.
                    if (_opt_postprocessors and not opt_pps_dropped
                            and "postprocessing" in error_str):
                        opt_pps_dropped = True
                        remaining = [pp for pp in (base_opts.get("postprocessors") or [])
                                     if pp not in _opt_postprocessors]
                        # base_opts too: video_opts is rebuilt from it on the
                        # next format rung, which would resurrect the extras.
                        base_opts["postprocessors"] = remaining
                        video_opts["postprocessors"] = remaining
                        # Sweep the failed postprocessor's `<stem>.temp.*`
                        # leftovers; the patterns never match the finished
                        # video, which the retry deliberately reuses.
                        _cleanup_partial_files(output_dir, file_stem)
                        logger.warning(
                            "Optional postprocessor failed, retrying without embed extras: %s", e
                        )
                        continue  # Retry same format without the extras

                    # ── Unknown error on first attempt: retry once ────
                    if attempt == 0:
                        logger.warning(f"Download error (attempt 1), retrying in 5s: {e}")
                        _cleanup_partial_files(output_dir, file_stem)
                        import time as _time
                        _time.sleep(5)
                        continue

                    # Second attempt also failed — try next format
                    logger.warning(f"Download failed on attempt 2 with format '{fmt}': {e}")
                    _cleanup_partial_files(output_dir, file_stem)
                    break  # Try next format

            if last_error is None:
                break  # Download succeeded

        # All formats exhausted — raise the last error
        if last_error is not None:
            _classify_and_raise(last_error)

        # Resolve actual video file (yt-dlp may change extension after merging)
        file_stem_resolved = file_stem or info.get("id", "")
        if video_path and not video_path.exists():
            actual = _find_actual_video_file(output_dir, file_stem_resolved)
            if actual:
                logger.info(f"Video file extension changed: expected {video_path.suffix}, found {actual.suffix}")
                video_path = actual

        # Extract subtitles from downloaded files
        subtitles = _collect_subtitles(output_dir, file_stem_resolved)

        # Clean up subtitle files after parsing (they're stored in DB now).
        #
        # EXCEPT when the caller asked for `subtitles: "file"` — then the
        # sidecar .srt IS the deliverable and sweeping it away would make the
        # option a silent no-op. "embed" still sweeps: the track is inside the
        # container by then, so leaving loose files would be litter.
        kept_subtitle_paths: list[str] = []
        if _opts.get("subtitles") == "file":
            for _ext in ("srt", "vtt"):
                kept_subtitle_paths.extend(
                    str(p) for p in output_dir.glob(f"{file_stem_resolved}.*.{_ext}")
                )
        else:
            _cleanup_subtitle_files(output_dir, file_stem_resolved)

        # Extract chapters from metadata
        chapters = _extract_chapters(info)

        # Extract tags and category
        tags = info.get("tags") or []
        category = info.get("categories", [None])[0] if info.get("categories") else info.get("category")

        # Audio extraction — extract locally from downloaded video (faster, no re-download)
        audio_dir = settings.AUDIO_DIR
        audio_dir.mkdir(parents=True, exist_ok=True)
        if extract_audio and video_path and video_path.exists():
            audio_path = _extract_audio_locally(video_path, audio_dir, file_stem_resolved)
            if not audio_path:
                # Fallback: re-download audio from YouTube
                logger.info("Local audio extraction failed, falling back to yt-dlp download")
                audio_opts = {
                    **_yt_dlp_base_opts(),
                    **_yt_dlp_js_opts(),
                    "format": "bestaudio/best",
                    "outtmpl": str(audio_dir / file_stem_resolved) + "_audio.%(ext)s",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "http_headers": _yt_dlp_http_headers(),
                }
                _apply_cookies(
                    audio_opts, cookie_browser, cookie_profile,
                    session_ok=cookie_session_ok,
                )
                try:
                    with yt_dlp.YoutubeDL(audio_opts) as ydl:
                        ydl.extract_info(url, download=True)
                        audio_path = audio_dir / f"{file_stem_resolved}_audio.mp3"
                except Exception as e:
                    logger.warning(f"Audio extraction failed (continuing without): {e}")

        file_size_mb = video_path.stat().st_size / (1024 * 1024) if video_path and video_path.exists() else 0

        # Probe the file that actually landed on disk. yt-dlp's envelope can
        # differ after merge/remux/fallback, while Phase-C QA compares source
        # and final media properties from the bytes we really own.
        media_metadata = {}
        if video_path and video_path.exists():
            from backend.services.video_utils import probe_media_details
            media_metadata = probe_media_details(video_path)

        return {
            "video_path": str(video_path) if video_path else None,
            "audio_path": str(audio_path) if audio_path and audio_path.exists() else None,
            "duration": duration,
            "file_size_mb": round(file_size_mb, 2),
            "title": info.get("title"),
            "views": info.get("view_count", 0),
            "likes": info.get("like_count", 0),
            "comments": info.get("comment_count", 0),
            "upload_date": info.get("upload_date"),  # "YYYYMMDD" string
            "uploader": info.get("uploader"),
            "uploader_url": info.get("uploader_url"),
            "thumbnail": info.get("thumbnail"),
            "description": info.get("description"),
            # New metadata fields
            "subtitles": subtitles,
            "chapters": chapters,
            "tags": tags,
            "category": category,
            # What actually landed on disk. Explicit quality choices are hard
            # ceilings in Phase C; the default path is source-best-first.
            "height": media_metadata.get("height") or info.get("height"),
            "media_metadata": media_metadata,
            "requested_quality": _opts.get("quality"),
            "kept_subtitle_paths": kept_subtitle_paths,
            # True when a postprocessing failure forced the embed extras
            # (thumbnail / subtitles / metadata) to be dropped so the video
            # itself could be kept — surfaced so the caller can report the
            # degradation instead of implying the extras were applied.
            "option_postprocessors_dropped": opt_pps_dropped,
        }

    try:
        return await asyncio.wait_for(asyncio.to_thread(_download), timeout=1200)  # 20 min max per video
    except asyncio.TimeoutError:
        raise DownloadError("Download timed out after 20 minutes. The video may be too large or the connection too slow.")


def _collect_subtitles(output_dir: Path, video_id: str) -> list[dict] | None:
    """Find downloaded subtitle files and parse the best one into text segments."""
    # yt-dlp saves subtitles as: {video_id}.{lang}.{ext}
    patterns = [
        str(output_dir / f"{video_id}.*.srt"),
        str(output_dir / f"{video_id}.*.vtt"),
    ]
    sub_files = []
    for pattern in patterns:
        sub_files.extend(glob.glob(pattern))

    if not sub_files:
        return None

    # Prefer creator subtitles (no "auto" in filename) over auto-generated
    creator_subs = [f for f in sub_files if ".auto." not in Path(f).name]
    chosen = creator_subs[0] if creator_subs else sub_files[0]

    try:
        text = Path(chosen).read_text(encoding="utf-8", errors="replace")
        segments = _parse_subtitle_text(text, chosen)

        # Detect language from filename (e.g. "video_id.en.srt" → "en")
        stem = Path(chosen).stem  # "video_id.en"
        parts = stem.split(".")
        lang = parts[-1] if len(parts) > 1 else None

        # Determine source type
        is_auto = ".auto." in Path(chosen).name or any(".auto." in f for f in sub_files if f == chosen)
        source = "auto_subtitles" if is_auto else "creator_subtitles"

        return {
            "text": _segments_to_text(segments),
            "language": lang,
            "source": source,
            "segments": segments,
            "file": str(chosen),
        }
    except Exception as e:
        logger.warning(f"Failed to parse subtitle file {chosen}: {e}")
        return None


def _parse_subtitle_text(content: str, filepath: str) -> list[dict]:
    """Parse SRT or VTT subtitle content into segments with timestamps."""

    segments = []

    if filepath.endswith(".vtt"):
        # WebVTT format
        # Skip header lines
        lines = content.split("\n")
        i = 0
        while i < len(lines) and not re.match(r"\d{2}:\d{2}", lines[i]):
            i += 1

        while i < len(lines):
            line = lines[i].strip()
            # Match timestamp line: 00:00:01.000 --> 00:00:04.000
            ts_match = re.match(
                r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})", line
            )
            if ts_match:
                start = _ts_to_seconds(ts_match.group(1))
                end = _ts_to_seconds(ts_match.group(2))
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip():
                    # Strip VTT tags like <c> </c> and positioning
                    cleaned = re.sub(r"<[^>]+>", "", lines[i].strip())
                    if cleaned:
                        text_lines.append(cleaned)
                    i += 1
                text = " ".join(text_lines).strip()
                if text:
                    segments.append({"start": start, "end": end, "text": text})
            else:
                i += 1
    else:
        # SRT format
        blocks = re.split(r"\n\s*\n", content.strip())
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 2:
                continue
            # Find timestamp line
            for j, line in enumerate(lines):
                ts_match = re.match(
                    r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})", line
                )
                if ts_match:
                    start = _ts_to_seconds(ts_match.group(1))
                    end = _ts_to_seconds(ts_match.group(2))
                    text = " ".join(l.strip() for l in lines[j + 1:] if l.strip())
                    text = re.sub(r"<[^>]+>", "", text)  # strip HTML tags
                    if text:
                        segments.append({"start": start, "end": end, "text": text})
                    break

    return segments


def _ts_to_seconds(ts: str) -> float:
    """Convert timestamp like '00:01:23,456' or '00:01:23.456' to seconds."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s


def _segments_to_text(segments: list[dict]) -> str:
    """Join subtitle segments into a single text string, deduplicating consecutive identical lines."""
    seen = []
    for seg in segments:
        text = seg["text"].strip()
        if not seen or text != seen[-1]:
            seen.append(text)
    return " ".join(seen)


def _extract_chapters(info: dict) -> list[dict] | None:
    """Extract chapter markers from yt-dlp metadata."""
    chapters = info.get("chapters")
    if not chapters:
        return None

    return [
        {
            "start": ch.get("start_time", 0),
            "end": ch.get("end_time", 0),
            "title": ch.get("title", ""),
        }
        for ch in chapters
        if ch.get("title")
    ]


async def get_video_info(url: str, flat: bool = False) -> dict:
    """Get video/channel metadata without downloading.
    Use flat=True for channels/playlists to avoid resolving every video."""
    cookie_browser, cookie_profile = await get_download_browser_session()
    cookie_session = await asyncio.to_thread(
        _inspect_browser_session, cookie_browser, cookie_profile
    )
    if _is_bytedance_url(url) and (
        not cookie_session.get("ok")
        or _required_bytedance_cookie_count(url, cookie_session) <= 0
    ):
        logger.warning(
            "ByteDance metadata probe blocked by missing/unreadable matching browser session"
        )
        return {}

    def _info():
        import yt_dlp
        opts = {**_yt_dlp_base_opts(), **_yt_dlp_js_opts(), "extract_flat": flat, "socket_timeout": 15,
                "http_headers": _yt_dlp_http_headers()}
        _apply_cookies(
            opts, cookie_browser, cookie_profile,
            session_ok=bool(cookie_session.get("ok")),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        return await asyncio.to_thread(_info)
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        return {}


async def list_channel_videos(url: str, max_videos: int = 5) -> list[dict]:
    """
    Extract video list from a channel/playlist URL without downloading.
    Returns list of dicts with video metadata (url, title, duration, views, etc).
    """
    cookie_browser, cookie_profile = await get_download_browser_session()
    cookie_session = await asyncio.to_thread(
        _inspect_browser_session, cookie_browser, cookie_profile
    )

    def _list():
        import yt_dlp
        opts = {
            **_yt_dlp_base_opts(),
            **_yt_dlp_js_opts(),
            "extract_flat": True,        # Don't download, just list
            "playlistend": max_videos,   # Limit number of videos
            "http_headers": _yt_dlp_http_headers(),
        }
        _apply_cookies(
            opts, cookie_browser, cookie_profile,
            session_ok=bool(cookie_session.get("ok")),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return []

        # Channel/playlist returns entries
        entries = info.get("entries", [])
        if not entries:
            # Single video URL — return as-is
            return [{
                "url": info.get("webpage_url") or info.get("url") or url,
                "video_id": info.get("id", ""),
                "title": info.get("title", ""),
                "duration": info.get("duration"),
                "view_count": info.get("view_count"),
                "upload_date": info.get("upload_date"),
            }]

        results = []
        for entry in entries:
            if not entry:
                continue
            video_url = entry.get("url") or entry.get("webpage_url", "")
            # Flat extraction often gives just video IDs — build full URL
            if video_url and not video_url.startswith("http"):
                video_url = f"https://www.youtube.com/watch?v={video_url}"
            results.append({
                "url": video_url,
                "video_id": entry.get("id", ""),
                "title": entry.get("title", ""),
                "duration": entry.get("duration"),
                "view_count": entry.get("view_count"),
                "upload_date": entry.get("upload_date"),
            })

        return results

    try:
        return await asyncio.to_thread(_list)
    except Exception as e:
        logger.error(f"Failed to list channel videos: {e}")
        return []


def is_channel_or_playlist_url(url: str) -> bool:
    """Check if a URL points to a channel or playlist rather than a single video."""
    indicators = [
        "/@", "/channel/", "/c/", "/user/",  # YouTube channels
        "/playlist?", "&list=",               # YouTube playlists
        "/videos",                            # Channel videos tab
    ]
    # A single video URL like youtube.com/watch?v=xxx without &list= is NOT a channel
    if "watch?v=" in url and "&list=" not in url:
        return False
    return any(ind in url for ind in indicators)
