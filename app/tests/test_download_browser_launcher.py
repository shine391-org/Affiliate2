from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.download_browser_session import normalize_douyin_cdp_url
from backend.services.download_browser_launcher import (
    affiliate2_douyin_profile_dir,
    discover_firefox_profiles,
    find_browser,
    find_supported_browser,
    launch_douyin_browser_session,
    open_cdp_page,
    open_cookie_source_browser,
    reset_managed_douyin_session,
    resolve_browser_executable,
)


def test_cdp_url_remains_localhost_only():
    assert normalize_douyin_cdp_url("http://127.0.0.1:9222") == "http://127.0.0.1:9222"
    with pytest.raises(ValueError, match="local"):
        normalize_douyin_cdp_url("http://192.168.1.20:9222")


def test_browser_detection_prefers_edge_common_windows_location(monkeypatch, tmp_path):
    program_files_x86 = tmp_path / "Program Files (x86)"
    edge = program_files_x86 / "Microsoft/Edge/Application/msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")

    monkeypatch.setenv("PROGRAMFILES(X86)", str(program_files_x86))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr("backend.services.download_browser_launcher._registry_app_path", lambda _browser: None)
    monkeypatch.setattr("backend.services.download_browser_launcher.shutil.which", lambda _: None)

    found = find_supported_browser()
    assert found is not None
    assert found.name == "edge"
    assert found.executable == edge


def test_dedicated_profile_is_persistent_and_affiliate2_owned(tmp_path):
    profile = affiliate2_douyin_profile_dir("edge", local_app_data=tmp_path)
    assert profile == tmp_path / "Affiliate2" / "browser" / "edge-douyin"
    assert "Default" not in profile.parts


def test_open_cdp_page_activates_the_created_browser_tab(monkeypatch):
    activated = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"id":"target-1","url":"https://www.douyin.com/",'
                b'"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/page/target-1"}'
            )

    monkeypatch.setattr(
        "backend.services.download_browser_launcher.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.activate_cdp_page",
        lambda websocket_url, target_id, timeout_s=2.0: activated.append(
            (websocket_url, target_id, timeout_s)
        ) or {"ok": True},
    )

    result = open_cdp_page("http://127.0.0.1:9222", "https://www.douyin.com/")

    assert result["ok"] is True
    assert result["page_activated"] is True
    assert activated == [
        ("ws://127.0.0.1:9222/devtools/page/target-1", "target-1", 2.0)
    ]


def test_open_cdp_page_surfaces_activation_failure(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"id":"target-1","url":"https://www.douyin.com/",'
                b'"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/page/target-1"}'
            )

    monkeypatch.setattr(
        "backend.services.download_browser_launcher.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.activate_cdp_page",
        lambda *_args, **_kwargs: {"ok": False, "error": "bring-to-front failed"},
    )

    result = open_cdp_page("http://127.0.0.1:9222", "https://www.douyin.com/")

    assert result["ok"] is False
    assert result["page_created"] is True
    assert result["page_activated"] is False
    assert result["error"] == "bring-to-front failed"


def test_selected_edge_never_falls_back_to_installed_chrome(monkeypatch, tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    chrome = local_app_data / "Google/Chrome/Application/chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")

    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr("backend.services.download_browser_launcher._registry_app_path", lambda _browser: None)
    monkeypatch.setattr("backend.services.download_browser_launcher.shutil.which", lambda _: None)

    assert find_browser("edge") is None
    found = find_browser("chrome")
    assert found is not None
    assert found.name == "chrome"
    assert found.executable == chrome


@pytest.mark.asyncio
async def test_already_running_cdp_does_not_launch_duplicate(monkeypatch, tmp_path):
    launches = []
    opened_pages = []
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: {
            "endpoint_reachable": True,
            "browser_product": "Edg/153.0",
        },
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda args, **kwargs: launches.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.open_cdp_page",
        lambda cdp_url, page_url: opened_pages.append((cdp_url, page_url)) or {
            "ok": True,
            "page_created": True,
            "page_activated": True,
        },
        raising=False,
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        data_dir=tmp_path,
    )

    assert result["ok"] is True
    assert result["status"] == "already_running"
    assert result["connected"] is True
    assert result["page_created"] is True
    assert result["page_activated"] is True
    assert launches == []
    assert opened_pages == [("http://127.0.0.1:9222", "https://www.douyin.com/")]


@pytest.mark.asyncio
async def test_already_running_cdp_reports_activation_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: {
            "endpoint_reachable": True,
            "browser_product": "Edg/153.0",
        },
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.open_cdp_page",
        lambda *_args, **_kwargs: {
            "ok": False,
            "page_created": True,
            "page_activated": False,
            "activation_error": "bring-to-front failed",
        },
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        data_dir=tmp_path,
    )

    assert result["ok"] is False
    assert result["connected"] is True
    assert result["page_created"] is True
    assert result["page_activated"] is False
    assert result["page_open_error"] == "bring-to-front failed"
    assert "could not bring its window to the front" in result["message"]


@pytest.mark.asyncio
async def test_selected_edge_repairs_chrome_occupied_cdp_port(monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"")
    launches = []
    launched = {"ready": False}
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_browser",
        lambda browser: type("Browser", (), {"name": browser, "executable": edge})(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_free_local_port",
        lambda: 9333,
    )

    def probe(url, *_args, **_kwargs):
        if url.endswith(":9222"):
            return {"endpoint_reachable": True, "browser_product": "Chrome/153.0"}
        return {
            "endpoint_reachable": launched["ready"],
            "browser_product": "Edg/153.0" if launched["ready"] else None,
        }

    monkeypatch.setattr("backend.services.download_browser_launcher.probe_cdp_endpoint", probe)
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda args, **kwargs: launches.append((args, kwargs)) or launched.update(ready=True) or type("P", (), {"pid": 1})(),
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        browser="edge",
        local_app_data=tmp_path,
        timeout_s=0.2,
        poll_interval_s=0,
    )

    assert result["ok"] is True
    assert result["status"] == "launched"
    assert result["connected"] is True
    assert result["port_repaired"] is True
    assert result["cdp_url"] == "http://127.0.0.1:9333"
    assert len(launches) == 1


@pytest.mark.asyncio
async def test_unsupported_browser_is_rejected_before_existing_port_probe(monkeypatch, tmp_path):
    probes = []
    launches = []
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: probes.append(True) or {
            "endpoint_reachable": True,
            "browser_product": "Edg/153.0",
        },
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda args, **kwargs: launches.append((args, kwargs)),
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        browser="firefox",
        local_app_data=tmp_path,
    )

    assert result["ok"] is False
    assert result["status"] == "unsupported_browser"
    assert probes == []
    assert launches == []


@pytest.mark.asyncio
async def test_launch_command_uses_port_and_dedicated_profile(monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"")
    probes = iter([
        {"endpoint_reachable": False},
        {"endpoint_reachable": True, "browser_product": "Edg/153.0"},
    ])
    commands = []

    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: next(probes),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_browser",
        lambda browser: type("Browser", (), {"name": browser, "executable": edge})(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda args, **kwargs: commands.append((args, kwargs)),
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9333",
        browser="edge",
        local_app_data=tmp_path,
        timeout_s=0.2,
        poll_interval_s=0,
    )

    assert result["status"] == "launched"
    args = commands[0][0]
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=9333" in args
    assert "--remote-allow-origins=*" in args
    assert f"--user-data-dir={tmp_path / 'Affiliate2' / 'browser' / 'edge-douyin'}" in args
    assert args[-1] == "https://www.douyin.com/"
    assert result["browser"] == "edge"
    assert result["executable"] == str(edge)
    assert result["profile_dir"] == str(tmp_path / "Affiliate2" / "browser" / "edge-douyin")


@pytest.mark.asyncio
async def test_selected_edge_is_passed_to_exact_browser_resolver(monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"")
    selected = []
    probes = iter([
        {"endpoint_reachable": False},
        {"endpoint_reachable": True, "browser_product": "Edg/153.0"},
    ])

    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: next(probes),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_browser",
        lambda browser: selected.append(browser) or type(
            "Browser", (), {"name": browser, "executable": edge}
        )(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda *_args, **_kwargs: object(),
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        browser="edge",
        local_app_data=tmp_path,
        timeout_s=0.2,
        poll_interval_s=0,
    )

    assert selected == ["edge"]
    assert result["browser"] == "edge"


@pytest.mark.asyncio
async def test_missing_browser_returns_structured_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: {"endpoint_reachable": False},
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_browser",
        lambda _browser: None,
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        data_dir=tmp_path,
    )

    assert result["ok"] is False
    assert result["status"] == "browser_not_found"
    assert result["connected"] is False
    assert "Edge" in result["message"]


@pytest.mark.asyncio
async def test_cdp_startup_timeout_is_structured(monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"")
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.probe_cdp_endpoint",
        lambda *_args, **_kwargs: {"endpoint_reachable": False},
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_supported_browser",
        lambda: type("Browser", (), {"name": "edge", "executable": edge})(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda *_args, **_kwargs: object(),
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        data_dir=tmp_path,
        timeout_s=0,
        poll_interval_s=0,
    )

    assert result["ok"] is False
    assert result["status"] == "cdp_timeout"
    assert result["connected"] is False


def test_canonical_resolver_returns_structured_detection_and_validates_override(monkeypatch, tmp_path):
    edge = tmp_path / "Browser Files" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")

    resolved = resolve_browser_executable("edge", override=str(edge))

    assert resolved["browser"] == "edge"
    assert resolved["found"] is True
    assert resolved["executable_path"] == str(edge)
    assert resolved["detection_source"] == "validated_override"
    assert resolved["validation"]["valid"] is True

    invalid = resolve_browser_executable("edge", override=str(tmp_path / "chrome.exe"))
    assert invalid["found"] is False
    assert invalid["error_code"] == "invalid_override"


def test_firefox_profiles_ini_discovery_prefers_default_release(tmp_path):
    firefox_root = tmp_path / "Mozilla" / "Firefox"
    profiles_root = firefox_root / "Profiles"
    default_release = profiles_root / "abc.default-release"
    stale = profiles_root / "stale.default"
    default_release.mkdir(parents=True)
    stale.mkdir(parents=True)
    (default_release / "cookies.sqlite").write_bytes(b"sqlite")
    (firefox_root / "profiles.ini").write_text(
        "[InstallABC]\nDefault=Profiles/abc.default-release\nLocked=1\n\n"
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abc.default-release\n\n"
        "[Profile1]\nName=default\nIsRelative=1\nPath=Profiles/stale.default\nDefault=1\n",
        encoding="utf-8",
    )

    profiles = discover_firefox_profiles(app_data=tmp_path)

    assert profiles[0]["name"] == "default-release"
    assert profiles[0]["path"] == str(default_release)
    assert profiles[0]["cookie_database_found"] is True
    assert all(item["path"] != str(stale) for item in profiles)


def test_open_tiktok_uses_selected_firefox_executable_and_profile(monkeypatch, tmp_path):
    firefox = tmp_path / "Mozilla Firefox" / "firefox.exe"
    profile = tmp_path / "Profiles" / "abc.default-release"
    firefox.parent.mkdir(parents=True)
    profile.mkdir(parents=True)
    firefox.write_bytes(b"MZ")
    commands = []
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda args, **kwargs: commands.append((args, kwargs)),
    )

    result = open_cookie_source_browser(
        "firefox",
        str(profile),
        executable_override=str(firefox),
    )

    assert result["ok"] is True
    assert commands[0][0] == [str(firefox), "-profile", str(profile), "https://www.tiktok.com/"]
    assert commands[0][1]["shell"] is False


@pytest.mark.asyncio
async def test_occupied_mismatched_cdp_port_is_repaired_with_free_endpoint(monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"MZ")
    commands = []
    probes = {
        "http://127.0.0.1:9222": {
            "endpoint_reachable": True,
            "browser_product": "Chrome/153.0",
        },
        "http://127.0.0.1:9444": {
            "endpoint_reachable": False,
            "browser_product": None,
        },
    }
    launched = {"ready": False}

    def fake_probe(url, *_args, **_kwargs):
        if url == "http://127.0.0.1:9444" and launched["ready"]:
            return {"endpoint_reachable": True, "browser_product": "Edg/153.0"}
        return probes[url]

    monkeypatch.setattr(
        "backend.services.download_browser_launcher.find_browser",
        lambda _browser, override=None: type("Browser", (), {"name": "edge", "executable": edge})(),
    )
    monkeypatch.setattr("backend.services.download_browser_launcher.find_free_local_port", lambda: 9444)
    monkeypatch.setattr("backend.services.download_browser_launcher.probe_cdp_endpoint", fake_probe)
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.Popen",
        lambda args, **kwargs: commands.append((args, kwargs)) or launched.update(ready=True) or type("P", (), {"pid": 1234})(),
    )

    result = await launch_douyin_browser_session(
        "http://127.0.0.1:9222",
        browser="edge",
        local_app_data=tmp_path,
        timeout_s=0.2,
        poll_interval_s=0,
    )

    assert result["ok"] is True
    assert result["cdp_url"] == "http://127.0.0.1:9444"
    assert result["port_repaired"] is True
    assert result["previous_cdp_url"] == "http://127.0.0.1:9222"
    assert "--remote-debugging-port=9444" in commands[0][0]


def test_reset_clears_only_the_selected_affiliate2_profile(monkeypatch, tmp_path):
    class ManagedProcess:
        pid = 4321

        @staticmethod
        def poll():
            return None

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        "backend.services.download_browser_launcher._MANAGED_DOUYIN_PROCESS",
        ManagedProcess(),
    )
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    edge_profile = affiliate2_douyin_profile_dir("edge", local_app_data=tmp_path)
    chrome_profile = affiliate2_douyin_profile_dir("chrome", local_app_data=tmp_path)
    edge_profile.mkdir(parents=True)
    chrome_profile.mkdir(parents=True)
    (edge_profile / "edge-marker").write_text("edge", encoding="utf-8")
    (chrome_profile / "chrome-marker").write_text("chrome", encoding="utf-8")

    result = reset_managed_douyin_session(
        confirm=True,
        clear_profile=True,
        browser="chrome",
    )

    assert result["ok"] is True
    assert result["profile_cleared"] is True
    assert result["profile_dir"] == str(chrome_profile)
    assert edge_profile.exists()
    assert not chrome_profile.exists()

