#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""
Affiliate2 Launcher — Windows one-click entry point.

Double-click Affiliate2.exe:
  1. Resolves its own location → release root
  2. Locates release/.venv/Scripts/python.exe (Python 3.11)
  3. Starts uvicorn (backend.main:app) on 127.0.0.1:PORT
  4. Polls /api/health until status == "ok" and app == "Affiliate2"
  5. Opens the browser to the real working URL
  6. Writes launcher.log to storage/logs/

Single-instance: a second double-click does NOT start a duplicate backend.
  - Sentinel socket on PORT-1 guards against two launcher processes.
  - Second instance: if backend already serving, opens browser; else error dialog.

Failure path: readable Windows MessageBoxW dialog + log written + no orphan backend.

Built with PyInstaller (console=False → no console window during normal startup).
"""

from __future__ import annotations

import ctypes
import json
import logging
import msvcrt
import os
import socket
import subprocess
import sys
import threading
import time
import atexit
import urllib.error
import urllib.request
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

PORT_ENV = "PORT"
DEFAULT_PORT = 16888
HOST_ENV = "HOST"
DEFAULT_HOST = "127.0.0.1"
SINGLE_INSTANCE_PORT_OFFSET = 1  # sentinel = PORT - 1

APP_NAME = "Affiliate2"
HEALTH_PATH = "/api/health"
EXPECTED_APP = "Affiliate2"

# ─── Bootstrap trace ──────────────────────────────────────────────────────────
# Writes to %LOCALAPPDATA%\Affiliate2\logs\launcher_bootstrap.log
# Works even in PyInstaller noconsole mode before any app logging is ready.

def _bootstrap_trace(stage: str, detail: str = "") -> None:
    try:
        base = Path(os.environ.get("LOCALAPPDATA", ""))
        if not base.exists():
            base = Path.home() / "AppData" / "Local"
        bootstrap_dir = base / "Affiliate2" / "logs"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        log_file = bootstrap_dir / "launcher_bootstrap.log"
        ts = time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"{ts} [{stage}] {detail}\n"
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


# ─── Path resolution ──────────────────────────────────────────────────────────

def _resolve_root() -> Path:
    """Find the release root relative to this executable / source file.

    Frozen (PyInstaller): sys._MEIPASS is the extraction dir; the spec's
    COLLECT places the exe + collateral there.  We walk up from the exe
    location instead — sys.executable is the .exe and the release root is
    its parent.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
    else:
        exe = Path(sys.argv[0]).resolve()
    # Affiliate2.exe sits directly in the release root (e.g.
    # E:\Affiliate ver 1\release\Affiliate2-v1.0.0\Affiliate2.exe).
    candidate = exe.parent
    if (candidate / ".venv" / "Scripts" / "python.exe").exists():
        return candidate
    # Fallback: walk up to find a directory that contains .venv + app/.
    for parent in candidate.parents:
        if (parent / ".venv" / "Scripts" / "python.exe").exists() and (
            parent / "app" / "backend" / "main.py"
        ).exists():
            return parent
    return candidate


# ─── Environment ──────────────────────────────────────────────────────────────

ROOT = _resolve_root()
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
FRONTEND_DIST = ROOT / "app" / "frontend" / "dist"
LOGS_DIR = ROOT / "storage" / "logs"
PID_FILE = ROOT / ".runtime" / "launcher-pid.json"

# Read configurable values from .env if present.
def _read_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    values: dict[str, str] = {}
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _get_port(env: dict[str, str]) -> int:
    raw = env.get(PORT_ENV)
    if raw and raw.isdigit():
        return int(raw)
    return DEFAULT_PORT


def _get_host(env: dict[str, str]) -> str:
    return env.get(HOST_ENV, DEFAULT_HOST) or DEFAULT_HOST


# ─── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "launcher.log"
    logger = logging.getLogger("Affiliate2.Launcher")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        fh.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        # Also mirror to stderr so a deliberately opened console sees output.
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger


log = _setup_logging()


def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """Fast TCP probe — returns True if something is bound to the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect((host, port))
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False


# ─── Single-instance guard ────────────────────────────────────────────────────

_LOCK_FILE = ROOT / ".runtime" / "launcher.lock"
_LOCK_FD = None  # open file descriptor; kept alive to hold the lock
_SENTINEL_SOCK = None  # kept open for the lifetime of this process


def _acquire_single_instance(port: int) -> bool:
    """Try to acquire the single-instance lock.

    Primary: bind a loopback sentinel socket on port-1.
    Fallback: Windows file-lock via msvcrt.locking().

    Returns True if this instance won the lock (should proceed),
    False if another instance already holds it.
    """
    global _SENTINEL_SOCK
    sentinel = port - SINGLE_INSTANCE_PORT_OFFSET
    log.info("Single-instance: attempting sentinel bind on 127.0.0.1:%d", sentinel)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", sentinel))
        s.listen(1)
        log.info("Single-instance: sentinel acquired on 127.0.0.1:%d", sentinel)
        _SENTINEL_SOCK = s  # keep open for process lifetime
        return True
    except OSError as exc:
        log.warning(
            "Single-instance: sentinel bind failed on 127.0.0.1:%d — %s; "
            "falling back to file-lock", sentinel, exc
        )
        try:
            s.close()
        except Exception:
            pass

    # File-lock fallback.
    global _LOCK_FD
    try:
        _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCK_FD = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            msvcrt.locking(_LOCK_FD, msvcrt.LK_NBLCK, 1)
            log.info("Single-instance: file-lock acquired (%s)", _LOCK_FILE)
            return True
        except OSError:
            os.close(_LOCK_FD)
            _LOCK_FD = None
            log.warning("Single-instance: file-lock already held — second instance")
            return False
    except Exception as exc:
        log.warning("Single-instance: file-lock fallback failed — %s", exc)
        return False


def _second_instance_action(port: int, host: str) -> None:
    """A second Affiliate2.exe double-click hit the sentinel.

    Try to open the existing UI; if the backend isn't serving, show an error.
    """
    url = f"http://{host}:{port}"
    try:
        req = urllib.request.Request(f"{url}{HEALTH_PATH}", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.load(resp)
            if data.get("status") == "ok" and data.get("app") == EXPECTED_APP:
                _open_browser(url)
                log.info(
                    "Second instance: backend already running on %s — opened browser", url
                )
                return
    except Exception:
        pass
    _show_error(
        f"{APP_NAME} is already running.",
        f"Another instance is active but the backend on {url}{HEALTH_PATH} "
        f"did not respond as expected.",
    )
    log.warning("Second instance detected; backend not reachable at %s", url)


def _release_lock() -> None:
    global _SENTINEL_SOCK, _LOCK_FD
    if _SENTINEL_SOCK is not None:
        try:
            _SENTINEL_SOCK.close()
        except Exception:
            pass
        _SENTINEL_SOCK = None
    if _LOCK_FD is not None:
        try:
            msvcrt.locking(_LOCK_FD, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            os.close(_LOCK_FD)
        except Exception:
            pass
        _LOCK_FD = None



# ─── Windows error dialog ─────────────────────────────────────────────────────

def _show_error(title: str, message: str) -> None:
    """MB_ICONERROR (0x10) + MB_OK = 0x10.  Blocking native dialog."""
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception as exc:
        log.error("MessageBoxW failed: %s", exc)


# ─── Backend lifecycle ────────────────────────────────────────────────────────

def _start_backend(
    python_exe: Path, port: int, host: str, env: dict[str, str]
) -> subprocess.Popen | None:
    """Spawn uvicorn as a separate process.  Returns the Popen or None on failure."""
    backend_env = os.environ.copy()
    backend_env["PORT"] = str(port)
    backend_env["HOST"] = host
    backend_env["VIRALMINT_DATA_DIR"] = str(ROOT)
    backend_env["VIRALMINT_FRONTEND_DIST"] = str(FRONTEND_DIST)
    backend_env["VIRALMINT_NO_BROWSER"] = "1"  # launcher owns browser open
    backend_env["PYTHONUTF8"] = "1"

    cmd = [
        str(python_exe),
        "-m",
        "uvicorn",
        "backend.main:app",
        f"--host={host}",
        f"--port={port}",
        "--log-level=info",
    ]

    # Hide the child console window on Windows.
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT / "app"),
            env=backend_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log.info("Backend spawned: uvicorn %s:%d (PID %d)", host, port, proc.pid)
        return proc
    except Exception as exc:
        log.error("Failed to spawn backend: %s", exc)
        return None


def _wait_for_health(port: int, host: str, timeout_s: float = 40.0) -> tuple[bool, str]:
    """Poll /api/health.  Returns (ok, detail)."""
    url = f"http://{host}:{port}{HEALTH_PATH}"
    deadline = time.time() + timeout_s
    last_detail = "no response yet"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                body = json.load(resp)
                status = body.get("status")
                app_name = body.get("app")
                if status == "ok" and app_name == EXPECTED_APP:
                    return True, f"status=ok app={app_name}"
                last_detail = f"status={status!r} app={app_name!r}"
        except urllib.error.HTTPError as exc:
            last_detail = f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_detail = str(exc)
        time.sleep(0.5)
    return False, last_detail


def _open_browser(url: str) -> None:
    """Best-effort browser open via ShellExecuteW."""
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "open", url, None, None, 1)
        log.info("Browser opened: %s", url)
    except Exception as exc:
        log.error("ShellExecuteW browser open failed: %s", exc)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    env = _read_env()
    port = _get_port(env)
    host = _get_host(env)
    url = f"http://{host}:{port}"

    log.info("%s launcher starting — root=%s port=%s:%d", APP_NAME, ROOT, host, port)

    # 1. Single-instance guard.
    if not _acquire_single_instance(port):
        _second_instance_action(port, host)
        return 1
    atexit.register(_release_lock)

    # If a backend is already bound to the service port, treat as running.
    if _port_listening(port, host):
        _open_browser(url)
        log.info(
            "Second instance: backend already listening on %s:%d — opened browser only",
            host, port,
        )
        return 0

    # 2. Pre-flight: python exe + frontend dist.
    if not VENV_PYTHON.is_file():
        _show_error(
            f"{APP_NAME} — Runtime not found",
            f"Could not find the Python runtime at:\n\n{VENV_PYTHON}\n\n"
            f"Reinstall/repair the release with setup.ps1.",
        )
        log.error("VENV_PYTHON missing: %s", VENV_PYTHON)
        return 1

    if not FRONTEND_DIST.is_dir():
        _show_error(
            f"{APP_NAME} — Frontend not built",
            f"Frontend dist is missing at:\n\n{FRONTEND_DIST}\n\n"
            f"Run `npm run build` inside app/frontend/ then relaunch.",
        )
        log.error("FRONTEND_DIST missing: %s", FRONTEND_DIST)
        return 1

    # 3. Start backend.
    proc = _start_backend(VENV_PYTHON, port, host, env)
    if proc is None:
        _show_error(
            f"{APP_NAME} — Failed to start",
            f"Could not launch the backend. Check launcher.log in storage/logs/.",
        )
        return 1

    # 4. Wait for health.
    ready, detail = _wait_for_health(port, host)
    if ready:
        log.info("Health check passed: %s", detail)
        _open_browser(url)
        _write_pid_file(port, host, proc.pid)
        log.info("%s ready and browser opened: %s", APP_NAME, url)
        return 0
    else:
        log.error("Health check failed after timeout: %s", detail)
        _show_error(
            f"{APP_NAME} — Backend did not start",
            f"The backend did not respond with status=ok at {url}{HEALTH_PATH} "
            f"within 40 seconds.\n\nLast check: {detail}\n\n"
            f"See launcher.log in storage/logs/ for details.",
        )
        # Best-effort cleanup: kill our own child.
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return 1


def _write_pid_file(port: int, host: str, backend_pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "app": APP_NAME,
        "host": host,
        "port": port,
        "backend_pid": backend_pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    PID_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
