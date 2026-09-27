"""Launch and reuse Affiliate2's dedicated local Douyin CDP browser."""
from __future__ import annotations

import asyncio
import configparser
import json
import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from backend.services.download_browser_session import normalize_douyin_cdp_url

DOUYIN_URL = "https://www.douyin.com/"
TIKTOK_URL = "https://www.tiktok.com/"
SUPPORTED_CDP_BROWSERS = ("edge", "chrome", "brave")
SUPPORTED_LOCAL_BROWSERS = (*SUPPORTED_CDP_BROWSERS, "firefox")
_EXPECTED_EXECUTABLES = {
    "edge": "msedge.exe",
    "chrome": "chrome.exe",
    "brave": "brave.exe",
    "firefox": "firefox.exe",
}
_MANAGED_DOUYIN_PROCESS: subprocess.Popen | None = None


@dataclass(frozen=True)
class BrowserExecutable:
    name: str
    executable: Path
    detection_source: str = "unknown"


def _local_app_data_root(local_app_data: Path | None = None) -> Path:
    if local_app_data is not None:
        return Path(local_app_data)
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local"


def affiliate2_douyin_profile_dir(
    browser: str = "edge",
    *,
    local_app_data: Path | None = None,
) -> Path:
    """Return the persistent Affiliate2-owned profile for one CDP browser."""
    selected = str(browser or "edge").strip().lower()
    if selected not in SUPPORTED_CDP_BROWSERS:
        selected = "edge"
    return _local_app_data_root(local_app_data) / "Affiliate2" / "browser" / f"{selected}-douyin"


def _registry_app_path(browser: str) -> Path | None:
    if platform.system() != "Windows":
        return None
    executable_name = _EXPECTED_EXECUTABLES.get(browser)
    if not executable_name:
        return None
    try:
        import winreg
    except ImportError:
        return None
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}"
    roots = (
        (winreg.HKEY_CURRENT_USER, 0),
        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_64KEY", 0)),
        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
    )
    for root, view in roots:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as key:
                value, _ = winreg.QueryValueEx(key, None)
                path = Path(str(value).strip('"'))
                if path.is_file():
                    return path
        except OSError:
            continue
    return None


def _windows_browser_candidates(browser: str) -> list[tuple[str, Path]]:
    roots = {
        "program_files_x86": os.environ.get("PROGRAMFILES(X86)"),
        "program_files": os.environ.get("PROGRAMFILES"),
        "local_app_data": os.environ.get("LOCALAPPDATA"),
    }
    locations = {
        "edge": (
            ("program_files_x86", "Microsoft/Edge/Application/msedge.exe"),
            ("program_files", "Microsoft/Edge/Application/msedge.exe"),
            ("local_app_data", "Microsoft/Edge/Application/msedge.exe"),
        ),
        "chrome": (
            ("program_files", "Google/Chrome/Application/chrome.exe"),
            ("program_files_x86", "Google/Chrome/Application/chrome.exe"),
            ("local_app_data", "Google/Chrome/Application/chrome.exe"),
        ),
        "brave": (
            ("program_files", "BraveSoftware/Brave-Browser/Application/brave.exe"),
            ("program_files_x86", "BraveSoftware/Brave-Browser/Application/brave.exe"),
            ("local_app_data", "BraveSoftware/Brave-Browser/Application/brave.exe"),
        ),
        "firefox": (
            ("program_files", "Mozilla Firefox/firefox.exe"),
            ("program_files_x86", "Mozilla Firefox/firefox.exe"),
            ("local_app_data", "Mozilla Firefox/firefox.exe"),
        ),
    }
    candidates: list[tuple[str, Path]] = []
    registry_path = _registry_app_path(browser)
    if registry_path is not None:
        candidates.append(("registry_app_paths", registry_path))
    for root_key, suffix in locations.get(browser, ()):
        root = roots[root_key]
        if root:
            candidates.append((root_key, Path(root) / suffix))
    return candidates


def _validate_browser_executable(browser: str, path: Path) -> dict:
    expected = _EXPECTED_EXECUTABLES.get(browser)
    actual = path.name.lower()
    valid_name = bool(expected and actual == expected)
    is_file = path.is_file()
    return {
        "valid": bool(valid_name and is_file),
        "is_file": is_file,
        "expected_filename": expected,
        "actual_filename": path.name,
    }


def resolve_browser_executable(browser: str, override: str | Path | None = None) -> dict:
    """Resolve one exact browser and explain where the executable came from."""
    selected = str(browser or "").strip().lower()
    result = {
        "browser": selected,
        "found": False,
        "executable_path": None,
        "detection_source": None,
        "validation": {"valid": False},
        "error_code": None,
        "error": None,
    }
    if selected not in SUPPORTED_LOCAL_BROWSERS:
        result.update(error_code="unsupported_browser", error=f"Unsupported browser: {selected}")
        return result

    if override:
        candidate = Path(override).expanduser()
        validation = _validate_browser_executable(selected, candidate)
        result["validation"] = validation
        if validation["valid"]:
            result.update(
                found=True,
                executable_path=str(candidate),
                detection_source="validated_override",
            )
        else:
            result.update(
                error_code="invalid_override",
                error=f"The selected file is not a valid {_EXPECTED_EXECUTABLES[selected]} executable.",
            )
        return result

    if platform.system() == "Windows":
        for source, candidate in _windows_browser_candidates(selected):
            validation = _validate_browser_executable(selected, candidate)
            if validation["valid"]:
                result.update(
                    found=True,
                    executable_path=str(candidate),
                    detection_source=source,
                    validation=validation,
                )
                return result

    binaries = {
        "edge": ("msedge.exe", "msedge", "microsoft-edge", "edge"),
        "chrome": ("chrome.exe", "chrome", "google-chrome"),
        "brave": ("brave.exe", "brave", "brave-browser"),
        "firefox": ("firefox.exe", "firefox"),
    }
    for binary in binaries[selected]:
        resolved = shutil.which(binary)
        if resolved:
            candidate = Path(resolved)
            validation = _validate_browser_executable(selected, candidate)
            if validation["is_file"]:
                validation["valid"] = True
                result.update(
                    found=True,
                    executable_path=str(candidate),
                    detection_source="path",
                    validation=validation,
                )
                return result

    result.update(
        error_code="browser_not_found",
        error=f"{_EXPECTED_EXECUTABLES[selected]} was not found in registry, known Windows locations, or PATH.",
    )
    return result


def find_browser(browser: str, override: str | Path | None = None) -> BrowserExecutable | None:
    """Resolve exactly the requested browser; never fall back to another one."""
    resolved = resolve_browser_executable(browser, override)
    if not resolved["found"]:
        return None
    return BrowserExecutable(
        resolved["browser"],
        Path(resolved["executable_path"]),
        str(resolved["detection_source"]),
    )


def find_supported_browser() -> BrowserExecutable | None:
    """Auto-detect a CDP browser without using the operating-system default."""
    for name in SUPPORTED_CDP_BROWSERS:
        resolved = find_browser(name)
        if resolved is not None:
            return resolved
    return None


def _firefox_root(app_data: Path | None = None) -> Path:
    if app_data is not None:
        return Path(app_data) / "Mozilla" / "Firefox"
    configured = os.environ.get("APPDATA")
    if configured:
        return Path(configured) / "Mozilla" / "Firefox"
    return Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox"


def discover_firefox_profiles(app_data: Path | None = None) -> list[dict]:
    """Parse profiles.ini and return existing profiles with readable cookie DBs first."""
    root = _firefox_root(app_data)
    ini_path = root / "profiles.ini"
    if not ini_path.is_file():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path, encoding="utf-8")
    except (OSError, configparser.Error):
        return []

    install_defaults = {
        parser.get(section, "Default", fallback="").replace("\\", "/")
        for section in parser.sections()
        if section.lower().startswith("install")
    }
    profiles: list[dict] = []
    seen: set[str] = set()
    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        stored_path = parser.get(section, "Path", fallback="").strip()
        if not stored_path:
            continue
        is_relative = parser.getboolean(section, "IsRelative", fallback=True)
        profile_path = (root / stored_path) if is_relative else Path(stored_path)
        try:
            profile_path = profile_path.resolve()
        except OSError:
            profile_path = profile_path.absolute()
        key = str(profile_path).lower()
        if key in seen or not profile_path.is_dir():
            continue
        seen.add(key)
        cookie_db = profile_path / "cookies.sqlite"
        has_cookie_db = cookie_db.is_file()
        if not has_cookie_db:
            continue
        name = parser.get(section, "Name", fallback=profile_path.name)
        normalized_stored = stored_path.replace("\\", "/")
        is_default = (
            parser.getboolean(section, "Default", fallback=False)
            or normalized_stored in install_defaults
        )
        profiles.append({
            "name": name,
            "path": str(profile_path),
            "is_default": is_default,
            "cookie_database_found": has_cookie_db,
            "cookie_database_path": str(cookie_db),
        })
    profiles.sort(
        key=lambda item: (
            not item["name"].lower().endswith("default-release"),
            not item["is_default"],
            item["name"].lower(),
        )
    )
    return profiles


def find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def open_cookie_source_browser(
    browser: str,
    profile: str | None,
    *,
    executable_override: str | Path | None = None,
) -> dict:
    selected = str(browser or "").strip().lower()
    resolved = resolve_browser_executable(selected, executable_override)
    if not resolved["found"]:
        return {"ok": False, **resolved}
    executable = str(resolved["executable_path"])
    if selected == "firefox":
        if not profile or not Path(profile).is_dir():
            return {
                "ok": False,
                "browser": selected,
                "profile": profile,
                "status": "profile_not_found",
                "message": "The selected Firefox profile could not be found.",
            }
        command = [executable, "-profile", str(profile), TIKTOK_URL]
    else:
        command = [executable, TIKTOK_URL]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "browser": selected,
            "profile": profile,
            "status": "launch_failed",
            "message": f"Could not launch {selected}: {type(exc).__name__}: {str(exc)[:240]}",
        }
    return {
        "ok": True,
        "browser": selected,
        "profile": profile,
        "executable": executable,
        "detection_source": resolved["detection_source"],
        "status": "launched",
    }


def probe_cdp_endpoint(cdp_url: str, timeout_s: float = 0.75) -> dict:
    endpoint = f"{cdp_url.rstrip('/')}/json/version"
    result = {
        "url": cdp_url,
        "endpoint_reachable": False,
        "browser_product": None,
        "websocket_url": None,
        "error": None,
    }
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
            result.update({
                "endpoint_reachable": 200 <= int(response.status) < 300,
                "browser_product": str(payload.get("Browser") or ""),
                "websocket_url": str(payload.get("webSocketDebuggerUrl") or ""),
            })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return result


def open_cdp_page(cdp_url: str, page_url: str, timeout_s: float = 2.0) -> dict:
    """Open a page inside the exact running CDP browser session."""
    endpoint = f"{cdp_url.rstrip('/')}/json/new?{quote(page_url, safe='')}"
    request = urllib.request.Request(endpoint, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {
                "ok": 200 <= int(response.status) < 300,
                "target_id": payload.get("id"),
                "url": payload.get("url") or page_url,
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


def _cdp_responds(cdp_url: str, timeout_s: float = 0.75) -> bool:
    return bool(probe_cdp_endpoint(cdp_url, timeout_s).get("endpoint_reachable"))


def _endpoint_matches_browser(metadata: dict, browser: str) -> bool:
    if browser == "auto":
        return True
    product = str(metadata.get("browser_product") or "").lower()
    if browser == "edge":
        return product.startswith("edg/") or "edge" in product
    if browser == "chrome":
        return (product.startswith("chrome/") or "google chrome" in product) and "edg/" not in product
    if browser == "brave":
        return "brave" in product
    return False


def describe_douyin_browser_runtime(
    browser: str,
    cdp_url: str,
    executable_override: str | Path | None = None,
) -> dict:
    selected = str(browser or "edge").strip().lower()
    resolved = (
        find_supported_browser()
        if selected == "auto"
        else find_browser(selected, executable_override) if executable_override else find_browser(selected)
    )
    resolved_name = resolved.name if resolved else (None if selected == "auto" else selected)
    profile_browser = resolved_name or ("edge" if selected == "auto" else selected)
    profile_dir = affiliate2_douyin_profile_dir(profile_browser)
    executable_result = resolve_browser_executable(profile_browser, executable_override)
    return {
        "selected_browser": selected,
        "resolved_browser": resolved_name,
        "executable_found": bool(resolved),
        "executable": str(resolved.executable) if resolved else None,
        "detection_source": executable_result.get("detection_source"),
        "executable_error": executable_result.get("error"),
        "profile_dir": str(profile_dir),
        "profile_ready": profile_dir.is_dir(),
        "cdp_url": cdp_url,
    }


def _base_result(cdp_url: str, runtime: dict) -> dict:
    return {
        **runtime,
        "cdp_url": cdp_url,
        "connected": False,
        "launched": False,
        "already_running": False,
        "port_repaired": False,
    }


async def launch_douyin_browser_session(
    cdp_url: str,
    *,
    browser: str = "edge",
    executable_override: str | Path | None = None,
    local_app_data: Path | None = None,
    data_dir: Path | None = None,
    timeout_s: float = 10.0,
    poll_interval_s: float = 0.25,
) -> dict:
    """Reuse a matching CDP endpoint or start the exact selected browser."""
    global _MANAGED_DOUYIN_PROCESS

    selected_url = normalize_douyin_cdp_url(cdp_url)
    selected_browser = str(browser or "edge").strip().lower()

    resolved = (
        find_supported_browser()
        if selected_browser == "auto"
        else find_browser(selected_browser, executable_override) if executable_override else find_browser(selected_browser)
    )
    resolved_name = resolved.name if resolved else (None if selected_browser == "auto" else selected_browser)
    profile_root = local_app_data if local_app_data is not None else data_dir
    profile_browser = resolved_name or ("edge" if selected_browser == "auto" else selected_browser)
    profile_dir = affiliate2_douyin_profile_dir(profile_browser, local_app_data=profile_root)
    runtime = {
        "selected_browser": selected_browser,
        "browser": resolved_name,
        "resolved_browser": resolved_name,
        "executable_found": bool(resolved),
        "executable": str(resolved.executable) if resolved else None,
        "detection_source": getattr(resolved, "detection_source", None) if resolved else None,
        "profile_dir": str(profile_dir),
        "profile_ready": profile_dir.is_dir(),
    }
    result = _base_result(selected_url, runtime)

    if selected_browser not in ("auto", *SUPPORTED_CDP_BROWSERS):
        return {
            **result,
            "ok": False,
            "status": "unsupported_browser",
            "message": f"{selected_browser or 'Selected browser'} does not support the Chromium CDP Douyin session.",
        }

    existing = await asyncio.to_thread(probe_cdp_endpoint, selected_url)
    if existing.get("endpoint_reachable"):
        if _endpoint_matches_browser(existing, selected_browser):
            opened_page = await asyncio.to_thread(open_cdp_page, selected_url, DOUYIN_URL)
            return {
                **result,
                "ok": True,
                "status": "already_running",
                "connected": True,
                "already_running": True,
                "browser_product": existing.get("browser_product"),
                "page_opened": bool(opened_page.get("ok")),
                "page_open_error": opened_page.get("error"),
                "message": "Affiliate2 Douyin browser session is already running.",
            }
        repaired_port = find_free_local_port()
        selected_url = normalize_douyin_cdp_url(f"http://127.0.0.1:{repaired_port}")
        result = {
            **_base_result(selected_url, runtime),
            "port_repaired": True,
            "previous_cdp_url": cdp_url,
        }

    if resolved is None:
        labels = {"edge": "Microsoft Edge", "chrome": "Google Chrome", "brave": "Brave"}
        label = labels.get(selected_browser, "Microsoft Edge, Google Chrome, or Brave")
        return {
            **result,
            "ok": False,
            "status": "browser_not_found",
            "blocking_reason": "browser_not_found",
            "message": f"Selected browser {label} was not found on this machine.",
        }

    port = urlparse(selected_url).port
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(resolved.executable),
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        DOUYIN_URL,
    ]

    try:
        _MANAGED_DOUYIN_PROCESS = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except Exception as exc:
        return {
            **result,
            "ok": False,
            "status": "launch_failed",
            "blocking_reason": "browser_launch_failed",
            "message": f"Could not launch {resolved.name}: {type(exc).__name__}: {str(exc)[:240]}",
        }

    deadline = time.monotonic() + max(timeout_s, 0)
    while True:
        metadata = await asyncio.to_thread(probe_cdp_endpoint, selected_url)
        if metadata.get("endpoint_reachable"):
            if not _endpoint_matches_browser(metadata, selected_browser):
                return {
                    **result,
                    "ok": False,
                    "status": "browser_mismatch",
                    "launched": True,
                    "blocking_reason": "browser_mismatch",
                    "message": f"CDP started with unexpected browser {metadata.get('browser_product')!r}.",
                }
            return {
                **result,
                "ok": True,
                "status": "launched",
                "connected": True,
                "launched": True,
                "profile_ready": True,
                "process_running": True,
                "managed_pid": getattr(_MANAGED_DOUYIN_PROCESS, "pid", None),
                "browser_product": metadata.get("browser_product"),
                "message": "Browser opened.",
            }
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(max(poll_interval_s, 0))

    return {
        **result,
        "ok": False,
        "status": "cdp_timeout",
        "launched": True,
        "profile_ready": True,
        "process_running": True,
        "blocking_reason": "cdp_unreachable",
        "message": f"Browser opened, but CDP did not become ready at {selected_url} within {timeout_s:g}s.",
    }


def reset_managed_douyin_session(
    *,
    confirm: bool,
    clear_profile: bool = False,
    browser: str = "edge",
) -> dict:
    """Stop only the process Affiliate2 launched in this backend lifetime."""
    global _MANAGED_DOUYIN_PROCESS
    normalized_browser = (browser or "edge").strip().lower()
    if normalized_browser not in SUPPORTED_CDP_BROWSERS:
        return {
            "ok": False,
            "status": "unsupported_browser",
            "message": f"{normalized_browser} is not a supported Douyin CDP browser.",
        }
    profile_dir = affiliate2_douyin_profile_dir(normalized_browser)
    if not confirm:
        return {"ok": False, "status": "confirmation_required"}
    process = _MANAGED_DOUYIN_PROCESS
    stopped = False
    if process is not None and process.poll() is None:
        try:
            if platform.system() == "Windows" and getattr(process, "pid", None):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            else:
                process.terminate()
            stopped = True
        except Exception as exc:
            return {"ok": False, "status": "stop_failed", "message": str(exc)[:240]}
    _MANAGED_DOUYIN_PROCESS = None
    profile_cleared = False
    if clear_profile:
        if not stopped:
            return {
                "ok": False,
                "status": "ownership_not_proven",
                "message": "Affiliate2 will not delete a browser profile unless it first stopped its own managed process.",
            }
        if profile_dir.is_dir():
            shutil.rmtree(profile_dir)
            profile_cleared = True
    return {
        "ok": True,
        "status": "reset",
        "stopped_managed_process": stopped,
        "profile_cleared": profile_cleared,
        "profile_dir": str(profile_dir),
    }

