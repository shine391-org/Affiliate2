from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from starlette.testclient import TestClient

from backend.main import create_app
from backend.messaging import manager as messaging_manager


def _cdp_unavailable_error(message):
    from backend.services.douyin_browser_session import DouyinCdpUnavailableError
    return DouyinCdpUnavailableError(message)


@pytest.fixture(scope="module")
def client():
    async def _noop(*args, **kwargs):
        return None

    messaging_manager.messaging.start_all = _noop  # type: ignore[assignment]
    messaging_manager.messaging.stop_all = _noop  # type: ignore[assignment]
    with TestClient(create_app()) as test_client:
        yield test_client


def _failed_cookie_probe(browser, profile):
    return {
        "ok": False,
        "browser": browser,
        "profile": profile,
        "error": "cookie decryption blocked",
        "douyin_cookie_count": 0,
        "tiktok_cookie_count": 0,
    }


def test_download_health_allows_authenticated_cdp_when_cookie_probe_fails(client, monkeypatch):
    async def fake_probe(browser, profile):
        return _failed_cookie_probe(browser, profile)

    @asynccontextmanager
    async def fake_open(*args, **kwargs):
        yield type("Session", (), {
            "cookie_dict": {"LOGIN_STATUS": "1"},
            "ms_token": "token",
            "user_agent": "Edge",
        })()

    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_probe)
    monkeypatch.setattr("backend.services.douyin_browser_session.open_douyin_cdp_session", fake_open)

    response = client.post("/api/settings/download-session/health", json={
        "browser": "edge",
        "profile": "Default",
        "douyin_cdp_url": "http://127.0.0.1:9222",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["douyin_cdp"]["connected"] is True
    assert body["douyin_cdp"]["authenticated"] is True
    assert body["douyin_ready"] is True
    assert body["tiktok_ready"] is False


def test_download_health_requires_cookie_or_authenticated_cdp(client, monkeypatch):
    async def fake_probe(browser, profile):
        return _failed_cookie_probe(browser, profile)

    @asynccontextmanager
    async def disconnected(*args, **kwargs):
        raise _cdp_unavailable_error(
            "Cannot connect to Douyin CDP at http://127.0.0.1:9222"
        )
        yield  # pragma: no cover

    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_probe)
    monkeypatch.setattr("backend.services.douyin_browser_session.open_douyin_cdp_session", disconnected)

    body = client.post("/api/settings/download-session/health", json={
        "browser": "edge",
        "douyin_cdp_url": "http://127.0.0.1:9222",
    }).json()

    assert body["douyin_cdp"]["connected"] is False
    assert body["douyin_ready"] is False
    assert body["tiktok_ready"] is False


def test_download_health_does_not_claim_closed_probe_page_is_reachable(client, monkeypatch):
    async def fake_probe(browser, profile):
        return _failed_cookie_probe(browser, profile)

    @asynccontextmanager
    async def no_persistent_douyin_page(*args, **kwargs):
        raise PermissionError(
            "No authenticated Douyin session found in any reusable CDP context."
        )
        yield  # pragma: no cover

    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_probe)
    monkeypatch.setattr(
        "backend.services.douyin_browser_session.open_douyin_cdp_session",
        no_persistent_douyin_page,
    )

    body = client.post("/api/settings/download-session/health", json={
        "browser": "edge",
        "douyin_cdp_url": "http://127.0.0.1:9222",
    }).json()

    assert body["douyin_cdp"]["connected"] is True
    assert body["douyin_cdp"]["websocket_connected"] is True
    assert body["douyin_cdp"]["page_reachable"] is False
    assert body["douyin_cdp"]["authenticated"] is False


def test_open_douyin_reports_auth_required_without_faking_failure(client, monkeypatch):
    observed = {}

    async def fake_launch(*args, **kwargs):
        observed.update(kwargs)
        return {
            "ok": True,
            "status": "launched",
            "cdp_url": "http://127.0.0.1:9222",
            "connected": True,
            "launched": True,
            "already_running": False,
        }

    @asynccontextmanager
    async def logged_out(*args, **kwargs):
        raise PermissionError("Douyin browser session is not logged in.")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)
    monkeypatch.setattr("backend.services.douyin_browser_session.open_douyin_cdp_session", logged_out)

    response = client.post(
        "/api/settings/download-session/open-douyin",
        json={
            "browser": "edge",
            "douyin_cdp_url": "http://127.0.0.1:9222",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "launched"
    assert body["connected"] is True
    assert body["authenticated"] is False
    assert body["auth_required"] is True
    assert "Log in" in body["message"]
    assert observed["browser"] == "edge"


def test_download_health_exposes_runtime_browser_details(client, monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    profile = tmp_path / "Affiliate2" / "browser" / "edge-douyin"

    async def fake_probe_browser_session(browser, profile_name):
        return _failed_cookie_probe(browser, profile_name)

    async def fake_cdp_probe(*args, **kwargs):
        return {
            "url": "http://127.0.0.1:9222",
            "endpoint_reachable": True,
            "websocket_connected": True,
            "page_reachable": True,
            "connected": True,
            "authenticated": False,
            "error": "Douyin browser session is not logged in.",
        }

    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_probe_browser_session)
    monkeypatch.setattr("backend.api.settings._probe_douyin_cdp", fake_cdp_probe)
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.describe_douyin_browser_runtime",
        lambda browser, cdp_url: {
            "selected_browser": browser,
            "resolved_browser": "edge",
            "executable": str(edge),
            "profile_dir": str(profile),
            "cdp_url": cdp_url,
        },
    )

    response = client.post("/api/settings/download-session/health", json={
        "browser": "edge",
        "douyin_cdp_url": "http://127.0.0.1:9222",
    })

    assert response.status_code == 200, response.text
    runtime = response.json()["browser_runtime"]
    assert runtime["selected_browser"] == "edge"
    assert runtime["resolved_browser"] == "edge"
    assert runtime["executable"] == str(edge)
    assert runtime["profile_dir"] == str(profile)


def test_open_douyin_reports_authenticated_session(client, monkeypatch):
    async def fake_launch(*args, **kwargs):
        return {
            "ok": True,
            "status": "already_running",
            "cdp_url": "http://127.0.0.1:9222",
            "connected": True,
            "launched": False,
            "already_running": True,
        }

    @asynccontextmanager
    async def authenticated(*args, **kwargs):
        yield type("Session", (), {
            "cookie_dict": {"LOGIN_STATUS": "1"},
            "ms_token": "token",
            "user_agent": "Edge",
        })()

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)
    monkeypatch.setattr("backend.services.douyin_browser_session.open_douyin_cdp_session", authenticated)

    body = client.post("/api/settings/download-session/open-douyin", json={
        "douyin_cdp_url": "http://127.0.0.1:9222",
    }).json()

    assert body["status"] == "already_running"
    assert body["connected"] is True
    assert body["authenticated"] is True
    assert body["auth_required"] is False


def test_download_health_honors_profile_without_explicit_browser(client, monkeypatch):
    observed = {}

    async def fake_probe(browser, profile):
        observed.update(browser=browser, profile=profile)
        return _failed_cookie_probe(browser, profile)

    @asynccontextmanager
    async def disconnected(*args, **kwargs):
        raise _cdp_unavailable_error("CDP unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_probe)
    monkeypatch.setattr("backend.services.douyin_browser_session.open_douyin_cdp_session", disconnected)

    response = client.post("/api/settings/download-session/health", json={
        "profile": "RequestedProfile",
        "douyin_cdp_url": "http://127.0.0.1:9222",
    })

    assert response.status_code == 200, response.text
    assert response.json()["profile"] == "RequestedProfile"
    assert observed["profile"] == "RequestedProfile"


def test_open_douyin_preserves_launcher_connection_when_probe_crashes(client, monkeypatch):
    async def fake_launch(*args, **kwargs):
        return {
            "ok": True,
            "status": "launched",
            "message": "Browser opened.",
            "cdp_url": "http://127.0.0.1:9222",
            "connected": True,
            "launched": True,
            "already_running": False,
        }

    @asynccontextmanager
    async def broken_probe(*args, **kwargs):
        raise RuntimeError("probe crashed")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)
    monkeypatch.setattr("backend.services.douyin_browser_session.open_douyin_cdp_session", broken_probe)

    response = client.post("/api/settings/download-session/open-douyin", json={
        "douyin_cdp_url": "http://127.0.0.1:9222",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["connected"] is True
    assert body["authenticated"] is False
    assert body["auth_required"] is False
    assert body["cdp_error"] == "RuntimeError: probe crashed"


def test_health_exposes_independent_structured_douyin_and_cookie_sections(client, monkeypatch, tmp_path):
    async def fake_cookie_probe(browser, profile):
        return {
            "ok": True,
            "browser": browser,
            "resolved_browser": "firefox",
            "profile": str(tmp_path / "profile"),
            "profile_found": True,
            "database_readable": True,
            "extraction_ok": True,
            "cookie_count": 62,
            "douyin_cookie_count": 0,
            "tiktok_cookie_count": 0,
            "tiktok_cookie_present": False,
            "tiktok_ready": False,
            "blocking_reason": "tiktok_login_required",
        }

    async def fake_cdp_probe(*args, **kwargs):
        return {
            "url": "http://127.0.0.1:9222",
            "endpoint_reachable": True,
            "websocket_connected": True,
            "page_reachable": True,
            "connected": True,
            "authenticated": True,
            "error": None,
        }

    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_cookie_probe)
    monkeypatch.setattr("backend.api.settings._probe_douyin_cdp", fake_cdp_probe)
    monkeypatch.setattr(
        "backend.services.download_browser_launcher.describe_douyin_browser_runtime",
        lambda browser, cdp_url, executable_override=None: {
            "selected_browser": "edge",
            "resolved_browser": "edge",
            "executable_found": True,
            "executable": "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "profile_dir": "C:/Users/test/AppData/Local/Affiliate2/browser/edge-douyin",
            "profile_ready": True,
            "cdp_url": cdp_url,
        },
    )

    body = client.post("/api/settings/download-session/health", json={
        "douyin_browser": "edge",
        "cookie_browser": "auto",
        "douyin_cdp_url": "http://127.0.0.1:9222",
    }).json()

    assert body["douyin_session"]["browser"] == "edge"
    assert body["douyin_session"]["ready"] is True
    assert body["douyin_session"]["blocking_reason"] is None
    assert body["cookie_source"]["browser"] == "firefox"
    assert body["cookie_source"]["extraction_ok"] is True
    assert body["cookie_source"]["tiktok_ready"] is False
    assert body["cookie_source"]["blocking_reason"] == "tiktok_login_required"
    assert body["douyin_session"]["ready"] is True


def test_open_douyin_uses_douyin_browser_not_cookie_provider(client, monkeypatch):
    observed = {}

    async def fake_launch(cdp_url, **kwargs):
        observed.update(cdp_url=cdp_url, **kwargs)
        return {
            "ok": False,
            "status": "browser_not_found",
            "cdp_url": cdp_url,
            "connected": False,
        }

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)

    response = client.post("/api/settings/download-session/open-douyin", json={
        "douyin_browser": "edge",
        "cookie_browser": "firefox",
    })

    assert response.status_code == 200
    assert observed["browser"] == "edge"


def test_open_tiktok_delegates_to_active_cookie_browser_and_profile(client, monkeypatch, tmp_path):
    profile = tmp_path / "profile.default-release"
    observed = {}

    monkeypatch.setattr(
        "backend.services.download_browser_launcher.open_cookie_source_browser",
        lambda browser, selected_profile, executable_override=None: observed.update(
            browser=browser,
            profile=selected_profile,
            executable_override=executable_override,
        ) or {"ok": True, "browser": browser, "profile": selected_profile},
    )

    response = client.post("/api/settings/download-session/open-tiktok", json={
        "cookie_browser": "firefox",
        "cookie_profile": str(profile),
    })

    assert response.status_code == 200, response.text
    assert observed["browser"] == "firefox"
    assert observed["profile"] == str(profile)


def test_repair_all_persists_actual_cdp_and_working_cookie_source(client, monkeypatch, tmp_path):
    firefox_profile = tmp_path / "profile.default-release"

    async def fake_launch(*args, **kwargs):
        return {
            "ok": True,
            "status": "launched",
            "browser": "edge",
            "resolved_browser": "edge",
            "executable": "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "profile_dir": "C:/Users/test/AppData/Local/Affiliate2/browser/edge-douyin",
            "cdp_url": "http://127.0.0.1:9444",
            "connected": True,
            "port_repaired": True,
        }

    async def fake_cookie_probe(*args, **kwargs):
        return {
            "ok": True,
            "resolved_browser": "firefox",
            "profile": str(firefox_profile),
            "profile_found": True,
            "database_readable": True,
            "extraction_ok": True,
            "cookie_count": 62,
            "douyin_cookie_count": 0,
            "tiktok_cookie_count": 0,
            "tiktok_cookie_present": False,
            "tiktok_ready": False,
            "blocking_reason": "tiktok_login_required",
        }

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)
    monkeypatch.setattr("backend.services.ytdlp_service.probe_browser_session", fake_cookie_probe)
    client.post("/api/settings", json={"download_cookie_browser": "auto", "download_cookie_profile": None})

    response = client.post("/api/settings/download-session/repair", json={"scope": "all"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["douyin_repair"]["cdp_url"] == "http://127.0.0.1:9444"
    assert body["cookie_repair"]["resolved_browser"] == "firefox"
    saved = client.get("/api/settings").json()
    assert saved["douyin_browser"] == "edge"
    assert saved["douyin_cdp_url"] == "http://127.0.0.1:9444"
    assert saved["download_cookie_browser"] == "firefox"
    assert saved["download_cookie_profile"] == str(firefox_profile)


def test_repair_douyin_discards_stale_executable_override_before_auto_detection(client, monkeypatch, tmp_path):
    stale = tmp_path / "msedge.exe"
    stale.write_bytes(b"edge")
    saved = client.post("/api/settings", json={
        "douyin_browser": "edge",
        "douyin_executable_path": str(stale),
    })
    assert saved.status_code == 200, saved.text
    stale.unlink()
    observed = {}

    async def fake_launch(cdp_url, **kwargs):
        observed.update(kwargs)
        return {
            "ok": True,
            "status": "launched",
            "resolved_browser": "edge",
            "executable": "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "cdp_url": cdp_url,
            "connected": True,
        }

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)

    response = client.post("/api/settings/download-session/repair", json={"scope": "douyin"})

    assert response.status_code == 200, response.text
    assert "executable_override" not in observed
    persisted = client.get("/api/settings").json()
    assert persisted["douyin_executable_path"].endswith("Microsoft/Edge/Application/msedge.exe")


def test_open_douyin_ignores_override_owned_by_another_selected_browser(client, monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"edge")
    saved = client.post("/api/settings", json={
        "douyin_browser": "edge",
        "douyin_executable_path": str(edge),
    })
    assert saved.status_code == 200, saved.text
    observed = {}

    async def fake_launch(cdp_url, **kwargs):
        observed.update(kwargs)
        return {"ok": False, "connected": False, "cdp_url": cdp_url}

    monkeypatch.setattr("backend.services.download_browser_launcher.launch_douyin_browser_session", fake_launch)

    response = client.post(
        "/api/settings/download-session/open-douyin",
        json={"douyin_browser": "chrome"},
    )

    assert response.status_code == 200, response.text
    assert observed["browser"] == "chrome"
    assert observed.get("executable_override") is None


def test_open_tiktok_ignores_override_owned_by_another_selected_browser(client, monkeypatch, tmp_path):
    firefox = tmp_path / "firefox.exe"
    firefox.write_bytes(b"firefox")
    saved = client.post("/api/settings", json={
        "download_cookie_browser": "firefox",
        "download_cookie_executable_path": str(firefox),
    })
    assert saved.status_code == 200, saved.text
    observed = {}

    def fake_open(browser, profile, **kwargs):
        observed.update({"browser": browser, "profile": profile, **kwargs})
        return {"ok": True, "status": "launched"}

    monkeypatch.setattr("backend.services.download_browser_launcher.open_cookie_source_browser", fake_open)

    response = client.post(
        "/api/settings/download-session/open-tiktok",
        json={"cookie_browser": "edge"},
    )

    assert response.status_code == 200, response.text
    assert observed["browser"] == "edge"
    assert observed.get("executable_override") is None


def test_open_tiktok_firefox_without_profile_auto_discovers_from_profiles_ini(client, monkeypatch):
    from backend.services import download_browser_launcher as launcher
    monkeypatch.setattr(
        launcher,
        "discover_firefox_profiles",
        lambda: [{"name": "default-release", "path": "/fake/firefox/profile", "is_default": True, "cookie_database_found": True}],
    )
    observed = {}

    def fake_open(browser, profile, **kwargs):
        observed.update({"browser": browser, "profile": profile, **kwargs})
        return {"ok": True, "status": "launched", "browser": browser, "profile": profile, "executable": "/fake/firefox/firefox.exe"}

    monkeypatch.setattr(launcher, "open_cookie_source_browser", fake_open)
    monkeypatch.setattr(launcher, "resolve_browser_executable", lambda browser, override=None: {"found": True, "executable_path": "/fake/firefox/firefox.exe", "detection_source": "test", "validation": {"valid": True}})

    response = client.post("/api/settings/download-session/open-tiktok", json={"cookie_browser": "firefox", "cookie_profile": ""})
    assert response.status_code == 200, response.text
    assert observed["browser"] == "firefox"
    assert observed["profile"] == "/fake/firefox/profile"


def test_open_tiktok_firefox_no_profiles_returns_profile_not_found(client, monkeypatch):
    from backend.services import download_browser_launcher as launcher
    monkeypatch.setattr(launcher, "discover_firefox_profiles", lambda: [])
    response = client.post("/api/settings/download-session/open-tiktok", json={"cookie_browser": "firefox", "cookie_profile": ""})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == "profile_not_found"

