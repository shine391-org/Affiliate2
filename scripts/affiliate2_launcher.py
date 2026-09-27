#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 Affiliate2 Contributors
"""
Affiliate2 Windows Launcher
============================
Double-click Affiliate2.exe → backend starts → health passes → browser opens.

Design notes:
  * Resolves its own install directory (the folder containing Affiliate2.exe).
  * Looks for .venv\\Scripts\\python.exe next to the exe; falls back to system
    Python if the venv has not been created yet (setup.ps1 must be run first).
  * Starts uvicorn as a hidden subprocess; no console window remains visible.
  * Polls /api/health until it returns 200 or the timeout expires.
  * On success: opens http://127.0.0.1:<PORT> in the Windows default browser.
  * Single-instance via a named mutex: if another Affiliate2.exe is already
    running, this instance silently opens the existing UI and exits.
  * On failure: MB_ICONERROR dialog + full details written to
    %LOCALAPPDATA%\\Affiliate2\\logs\\startup.log
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

PORT_DEFAULT = 16888
HOST_DEFAULT = "127.0.0.1"
HEALTH_URL = "/api/health"
HEALTH_TIMEOUT_S = 60          # total time to wait for the backend to come up
HEALTH_POLL_S = 0.5            # interval between probes
BROWSER_OPEN_DELAY_S = 1.0     # brief pause after health OK before opening tab
LOG_DIR = Path(os.environ.get(
    "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
)) / "Affiliate2" / "logs"
LOG_FILE = LOG_DIR / "startup.log"

APP_NAME = "Affiliate2"
MUTEX_NAME = "Global\\Affiliate2LauncherSingleInstance"


# ── Helpers ─────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    """Append a timestamped line to the startup log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def log_exception(exc: BaseException, context: str = "") -> None:
    """Write an exception with a traceback-like summary to the log."""
    import traceback
    tb = exc.__traceback__
    lines = traceback.format_exception(type(exc), exc, tb)
    text = "".join(lines).rstrip()
    prefix = f"[{context}] " if context else ""
    log(f"{prefix}EXCEPTION:\n{text}")


def show_error_dialog(title: str, message: str) -> None:
    """Show a native Windows error dialog (MB_ICONERROR). Non-Windows: print."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                message,
                title,
                0x10,   # MB_ICONERROR
            )
        except Exception:
            print(f"{title}: {message}", file=sys.stderr)
    else:
        print(f"{title}: {message}", file=sys.stderr)


def find_install_dir() -> Path:
    """
    Resolve the directory that contains Affiliate2.exe.

    Frozen (PyInstaller one-file): sys._MEIPASS is the temp extract dir — we
    want the *original* exe location, not the temp dir.  Use the exe path.
    """
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
    else:
        exe_path = Path(__file__).resolve()
    return exe_path.parent


def find_python(install_dir: Path) -> Path | None:
    """
    Locate the Python interpreter to use for the backend.

    Priority:
      1. .venv\Scripts\python.exe  (next to the exe — created by setup.ps1)
      2. py launcher (Windows Python Launcher, always available when Python
         is installed via python.org)
      3. system python3 / python on PATH
    """
    # 1. Bundled venv
    venv_python = install_dir / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python

    # 2. py launcher
    try:
        result = subprocess.run(
            ["py", "-3.11", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path("py")  # 'py' on PATH — resolves to the right version
    except Exception:
        pass

    # 3. system python
    for name in ("python3", "python"):
        try:
            result = subprocess.run(
                [name, "--version"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return Path(name)
        except FileNotFoundError:
            continue

    return None


def read_env_file(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (no quoting bells/whistles)."""
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def port_listening(host: str, port: int) -> bool:
    """TCP probe — returns True if anything accepts connections on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect((host, port))
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False


def wait_for_health(url: str, timeout_s: float, poll_s: float) -> bool:
    """Block until /api/health returns 200 or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if getattr(resp, "status", 200) == 200:
                    return True
        except Exception:
            pass
        time.sleep(poll_s)
    return False


def open_browser(url: str) -> None:
    """Open url in the Windows default browser."""
    log(f"Opening browser: {url}")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception as e:
        log(f"webbrowser.open failed: {e}")
        show_error_dialog(
            f"{APP_NAME} — Browser Error",
            f"Backend is running at {url}\n\n"
            f"However, opening the browser failed: {e}\n\n"
            f"Please open {url} manually in your browser.",
        )


# ── Single-instance ─────────────────────────────────────────────────────────────

def acquire_single_instance() -> ctypes.c_void_p | None:
    """
    Create a named mutex. Returns the mutex handle on success, None if another
    instance already holds it.

    On Windows a 'Global\\' prefix makes the mutex visible across sessions
    (Terminal Services / Fast User Switching). For a single-user desktop app
    'Local\\' would also work, but 'Global\\' is the safer default.
    """
    if sys.platform != "win32":
        return None  # no-op on non-Windows
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(
        ctypes.c_void_p(None),  # default security
        False,                   # initially not owned
        MUTEX_NAME,
    )
    last_error = kernel32.GetLastError()
    # ERROR_ALREADY_EXISTS (183) = another instance is running
    if last_error == 183:
        if mutex:
            kernel32.CloseHandle(mutex)
        return None
    return mutex


# ── Backend lifecycle ───────────────────────────────────────────────────────────

class Backend:
    """Manages the uvicorn subprocess + health polling."""

    def __init__(self, install_dir: Path, python_exe: Path,
                 host: str, port: int, env: dict[str, str]):
        self.install_dir = install_dir
        self.python_exe = python_exe
        self.host = host
        self.port = port
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.url = f"http://{host}:{port}"

    def start(self) -> bool:
        """
        Start uvicorn as a hidden subprocess.
        Returns True if the process was launched (it may still fail to bind).
        """
        app_dir = self.install_dir / "app"
        if not app_dir.exists():
            raise FileNotFoundError(
                f"App directory not found: {app_dir}\n"
                f"Install dir: {self.install_dir}"
            )

        cmd = [
            str(self.python_exe),
            "-m", "uvicorn",
            "backend.main:app",
            f"--host={self.host}",
            f"--port={self.port}",
            "--log-level=info",
        ]

        log(f"Starting backend: {' '.join(cmd)}")
        log(f"Working directory: {app_dir}")
        log(f"Python: {self.python_exe}")

        # Windows: hide the subprocess console window.
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        self.proc = subprocess.Popen(
            cmd,
            cwd=str(app_dir),
            env=self.env,
            startupinfo=startupinfo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log(f"Backend process started (PID {self.proc.pid})")
        return True

    def wait_for_health(self) -> bool:
        """Poll /api/health; return True when 200 is received."""
        url = f"{self.url}{HEALTH_URL}"
        log(f"Waiting for {url} (timeout {HEALTH_TIMEOUT_S}s)...")
        return wait_for_health(url, HEALTH_TIMEOUT_S, HEALTH_POLL_S)

    def read_log_tail(self, lines: int = 50) -> str:
        """Read the last N lines from the backend stdout pipe."""
        if self.proc is None or self.proc.stdout is None:
            return ""
        try:
            self.proc.stdout.seek(0, 2)  # end of file
            pos = self.proc.stdout.tell()
            chunk_size = 4096
            data = b""
            while pos > 0 and len(data) < lines * 200:
                read_pos = max(0, pos - chunk_size)
                self.proc.stdout.seek(read_pos)
                chunk = self.proc.stdout.read(pos - read_pos)
                data = chunk + data
                pos = read_pos
                if not chunk:
                    break
            text = data.decode("utf-8", errors="replace")
            return "\n".join(text.splitlines()[-lines:])
        except Exception as e:
            return f"(could not read backend log: {e})"


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    log(f"Affiliate2 launcher starting (frozen={getattr(sys, 'frozen', False)})")

    # ── Single-instance check ──────────────────────────────────────────────────
    mutex = acquire_single_instance()
    if mutex is None:
        log("Another Affiliate2 launcher instance is already running — "
            "opening existing UI and exiting.")
        # Backend may or may not be up; try the default URL first.
        try:
            import webbrowser
            webbrowser.open(f"http://{HOST_DEFAULT}:{PORT_DEFAULT}")
        except Exception:
            pass
        log("Done (existing instance branch).")
        return 0

    try:
        # ── Resolve install directory ──────────────────────────────────────────
        install_dir = find_install_dir()
        log(f"Install directory: {install_dir}")

        # ── Find Python ────────────────────────────────────────────────────────
        python_exe = find_python(install_dir)
        if python_exe is None:
            msg = (
                f"{APP_NAME} could not find a Python interpreter.\n\n"
                f"Please run setup.ps1 first to create the Python environment.\n\n"
                f"Install directory: {install_dir}\n"
                f"Expected: {install_dir / '.venv' / 'Scripts' / 'python.exe'}"
            )
            show_error_dialog(f"{APP_NAME} — Setup Required", msg)
            log(msg)
            return 1
        log(f"Using Python: {python_exe}")

        # ── Read .env ──────────────────────────────────────────────────────────
        env_file = install_dir / ".env"
        env_vars = read_env_file(env_file)
        log(f".env: {env_file} (exists={env_file.exists()})")

        host = env_vars.get("HOST", HOST_DEFAULT)
        port = int(env_vars.get("PORT", str(PORT_DEFAULT)))

        # ── Build environment ──────────────────────────────────────────────────
        proc_env = os.environ.copy()
        proc_env["VIRALMINT_DATA_DIR"] = str(install_dir)
        proc_env["CONTENT_ENGINE_ROOT"] = str(install_dir)
        proc_env["VIRALMINT_NO_BROWSER"] = "1"   # launcher owns browser open
        proc_env["DEBUG"] = "false"
        proc_env["PYTHONUTF8"] = "1"
        proc_env["HOST"] = host
        proc_env["PORT"] = str(port)

        # Forward any HTTP proxy / AI provider vars from .env
        for key in ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL",
                     "AI_PROVIDER", "AFFILIATE2_DOUYIN_CDP_URL",
                     "HF_HOME", "HUGGINGFACE_TOKEN"):
            if key in env_vars:
                proc_env[key] = env_vars[key]

        # ── Start backend ──────────────────────────────────────────────────────
        backend = Backend(install_dir, python_exe, host, port, proc_env)
        if not backend.start():
            raise RuntimeError("Failed to launch backend process")

        # ── Wait for health ────────────────────────────────────────────────────
        healthy = backend.wait_for_health()
        if not healthy:
            tail = backend.read_log_tail(40)
            msg = (
                f"{APP_NAME} failed to start within {HEALTH_TIMEOUT_S} seconds.\n\n"
                f"Backend URL: {backend.url}\n"
                f"Health check: {backend.url}{HEALTH_URL}\n\n"
                f"Backend log tail:\n{tail}"
            )
            show_error_dialog(f"{APP_NAME} — Startup Failed", msg)
            log(f"HEALTH CHECK FAILED. Log tail:\n{tail}")
            return 1

        log(f"Backend is healthy at {backend.url}")

        # ── Open browser ───────────────────────────────────────────────────────
        time.sleep(BROWSER_OPEN_DELAY_S)
        open_browser(backend.url)

        log("Launcher done — backend running, browser opened.")
        return 0

    except Exception as e:
        log_exception(e, "LAUNCHER FATAL")
        import traceback
        details = traceback.format_exc()
        msg = (
            f"{APP_NAME} could not start.\n\n"
            f"Error: {e}\n\n"
            f"Full details have been written to:\n{LOG_FILE}\n\n"
            f"Please check that setup.ps1 has been run and that Python 3.11\n"
            f"and all required dependencies are installed."
        )
        show_error_dialog(f"{APP_NAME} — Startup Error", msg)
        # Also write the full traceback to the log.
        log(f"FULL TRACEBACK:\n{details}")
        return 1

    finally:
        if mutex is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(mutex)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
