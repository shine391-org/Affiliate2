"""CSRF origin checks remain active when the server is exposed on the LAN."""
from __future__ import annotations

import asyncio

import httpx

from backend.config import settings
from backend.main import create_app


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
    assert same_origin.status_code == 404


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
