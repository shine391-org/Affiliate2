"""CSRF origin checks remain active when the server is exposed on the LAN."""
from __future__ import annotations

import asyncio

import httpx

from backend.config import settings
from backend.main import create_app


def test_local_mode_allows_vite_on_127_loopback(monkeypatch):
    monkeypatch.setattr(settings, "HOST", "127.0.0.1")
    app = create_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:16888",
        ) as client:
            post = await client.post(
                "/api/definitely-not-a-route",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            preflight = await client.options(
                "/api/settings/download-session/health",
                headers={
                    "Origin": "http://127.0.0.1:5173",
                    "Access-Control-Request-Method": "POST",
                },
            )
            wrong_port = await client.post(
                "/api/definitely-not-a-route",
                headers={"Origin": "http://127.0.0.1:9000"},
            )
            return post, preflight, wrong_port

    post, preflight, wrong_port = asyncio.run(run())
    assert post.status_code != 403
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert wrong_port.status_code == 403


def test_packaged_loopback_same_origin_uses_request_host_not_stale_configured_port(monkeypatch):
    monkeypatch.setattr(settings, "HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "PORT", 16888)
    app = create_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:16996",
        ) as client:
            return await client.post(
                "/api/definitely-not-a-route",
                headers={"Origin": "http://127.0.0.1:16996"},
            )

    response = asyncio.run(run())
    assert response.status_code != 403


def test_lan_mode_allows_same_host_but_blocks_cross_site(monkeypatch):
    monkeypatch.setattr(settings, "HOST", "0.0.0.0")
    app = create_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://192.168.1.10:16888",
        ) as client:
            blocked = await client.post(
                "/api/definitely-not-a-route",
                headers={"Origin": "https://evil.example"},
            )
            same_origin = await client.post(
                "/api/definitely-not-a-route",
                headers={"Origin": "http://192.168.1.10:16888"},
            )
            return blocked, same_origin

    blocked, same_origin = asyncio.run(run())
    assert blocked.status_code == 403
    assert same_origin.status_code != 403


def test_lan_mode_rejects_same_origin_public_hostname_dns_rebinding(monkeypatch):
    monkeypatch.setattr(settings, "HOST", "0.0.0.0")
    app = create_app()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://evil.example:16888",
        ) as client:
            return await client.post(
                "/api/definitely-not-a-route",
                headers={"Origin": "http://evil.example:16888"},
            )

    response = asyncio.run(run())
    assert response.status_code == 403
