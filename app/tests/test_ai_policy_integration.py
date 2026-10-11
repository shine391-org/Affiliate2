# SPDX-License-Identifier: AGPL-3.0-only
"""Settings HTTP -> SQLite -> canonical C6/C7 SDK, controlled local upstream."""
import asyncio
import json
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from aiohttp import web
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api import settings as settings_api
from backend.core.ai_provider import get_ai_client
from backend.core.localization import LocalizationProfile
from backend.models.user_settings import UserSettings
from backend.services.localization_service import _profile_ai_route_clients


@asynccontextmanager
async def isolated_api(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'policy.db'))
    async with engine.begin() as connection:
        await connection.run_sync(UserSettings.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings_api, 'AsyncSessionLocal', sessions)
    app = FastAPI()
    app.include_router(settings_api.router, prefix='/api')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://qa') as api:
            yield api, sessions
    finally:
        await engine.dispose()


@asynccontextmanager
async def controlled_upstream():
    rows = []
    async def complete(request):
        payload = await request.json()
        row = {'start': time.monotonic(), 'model': payload['model']}
        rows.append(row)
        await asyncio.sleep(0.01)
        row['end'] = time.monotonic()
        return web.json_response({'id': 'qa', 'object': 'chat.completion', 'created': 1,
            'model': payload['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': 'Xin chào.'}}]})
    app = web.Application()
    app.router.add_post('/v1/chat/completions', complete)
    runner = web.AppRunner(app, shutdown_timeout=0.2)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        yield f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1', rows
    finally:
        await runner.cleanup()


def connection(base='http://127.0.0.1:9/v1', **extra):
    return dict(label='Paced', provider='compatible', model='qa', base_url=base,
                traffic_policy={'enabled': True, 'min_gap_s': 0.08,
                    'cooldown_initial_s': 0.1, 'cooldown_max_s': 0.3}, **extra)


async def saved(sessions):
    async with sessions() as db:
        return (await db.execute(select(UserSettings).where(UserSettings.user_id == 'local'))).scalar_one()


@pytest.mark.asyncio
async def test_real_settings_sqlite_roundtrip_and_pinned_c6_c7_dispatch(tmp_path, monkeypatch):
    async with controlled_upstream() as (base, rows), isolated_api(tmp_path, monkeypatch) as (api, sessions):
        created = await api.post('/api/settings/ai/connections', json=connection(base))
        assert created.status_code == 200, created.text
        policy = created.json()['ai_connections']['paced']['traffic_policy']
        assert policy['enabled'] is True
        await api.post('/api/settings/ai/connections', json=dict(connection(base),
            label='Alias', activate=False))
        readback = (await api.get('/api/settings')).json()
        assert readback['ai_connections']['paced']['traffic_policy'] == policy
        row = await saved(sessions)
        assert json.loads(row.ai_connections_json)['paced']['traffic_policy'] == policy
        profile = LocalizationProfile(id='qa', name='QA', translation_routes=[{'connection_id': 'alias', 'model': 'c6'}],
            rewrite_routes=[{'connection_id': 'paced', 'model': 'c7'}])
        c6 = _profile_ai_route_clients(row, profile, 'translation')[0]
        c7 = _profile_ai_route_clients(row, profile, 'rewrite')[0]
        assert c6.connection_name == 'alias' and c7.connection_name == 'paced'
        assert c6.traffic_policy == c7.traffic_policy == get_ai_client(row).traffic_policy == policy
        replies = await asyncio.gather(*(ai.chat(messages=[{'role': 'user', 'content': 'Hello'}])
                                        for ai in (c6, c7)))
        assert replies == ['Xin chào.'] * 2
        assert {r['model'] for r in rows} == {'c6', 'c7'}
        assert rows[1]['start'] - rows[0]['end'] >= 0.07
        assert 'api_key_encrypted' not in readback['ai_connections']['paced']


@pytest.mark.asyncio
async def test_http_edit_omit_preserves_null_clears_without_rebinding(tmp_path, monkeypatch):
    async with isolated_api(tmp_path, monkeypatch) as (api, sessions):
        created = await api.post('/api/settings/ai/connections', json=connection())
        assert created.status_code == 200
        before = json.loads((await saved(sessions)).ai_connections_json)['paced']
        edit = {'connection_id': 'paced', 'label': 'Renamed', 'provider': 'compatible'}
        assert (await api.post('/api/settings/ai/connections', json=edit)).status_code == 200
        omitted = json.loads((await saved(sessions)).ai_connections_json)['paced']
        assert omitted['traffic_policy'] == before['traffic_policy']
        assert omitted['model'] == before['model'] and omitted['base_url'] == before['base_url']
        assert (await api.post('/api/settings/ai/connections', json=dict(edit, traffic_policy=None))).status_code == 200
        cleared = json.loads((await saved(sessions)).ai_connections_json)['paced']
        assert cleared['traffic_policy'] is None
        assert get_ai_client(await saved(sessions)).traffic_policy is None


@pytest.mark.asyncio
async def test_http_invalid_policy_rejected_without_persisting_changes(tmp_path, monkeypatch):
    async with isolated_api(tmp_path, monkeypatch) as (api, sessions):
        assert (await api.post('/api/settings/ai/connections', json=connection())).status_code == 200
        before = (await saved(sessions)).ai_connections_json
        for invalid in ({'enabled': 'yes'}, {'min_gap_s': -1}, {'bogus': True}):
            response = await api.post('/api/settings/ai/connections', json=dict(connection(),
                connection_id='paced', traffic_policy=invalid))
            assert response.status_code == 400, response.text
            assert (await saved(sessions)).ai_connections_json == before


@pytest.mark.asyncio
async def test_saved_probe_unsaved_override_and_null_clear_reach_real_sdk(tmp_path, monkeypatch):
    async with controlled_upstream() as (base, rows), isolated_api(tmp_path, monkeypatch) as (api, sessions):
        assert (await api.post('/api/settings/ai/connections', json=connection(base))).status_code == 200
        before = (await saved(sessions)).ai_connections_json
        override = {'enabled': True, 'min_gap_s': 0.14, 'cooldown_initial_s': 0.1, 'cooldown_max_s': 0.3}
        for body in ({'connection_id': 'paced'}, {'connection_id': 'paced', 'traffic_policy': override},
                     {'connection_id': 'paced', 'traffic_policy': None}):
            response = await api.post('/api/settings/ai/test', json=body)
            assert response.status_code == 200, response.text
        assert len(rows) == 3
        assert rows[1]['start'] - rows[0]['end'] >= 0.13
        assert (await saved(sessions)).ai_connections_json == before
        for invalid in ({'enabled': 'yes'}, {'cooldown_initial_s': -1}):
            response = await api.post('/api/settings/ai/test', json={'connection_id': 'paced', 'traffic_policy': invalid})
            assert response.status_code == 400
        assert len(rows) == 3
