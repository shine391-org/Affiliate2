"""LOCAL API/SQLite policy persistence; no provider/network/media mocked success."""
import json
import pytest
import httpx
from fastapi import FastAPI
from backend.api import settings as settings_api
from backend.core.localization import LocalizationProfile
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.user_settings import UserSettings


@pytest.mark.asyncio
async def test_auto_policy_saved_and_reloaded_through_real_settings_router():
    from sqlalchemy import select
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UserSettings).where(UserSettings.user_id == 'local'))).scalar_one_or_none()
        if row is None:
            row = UserSettings(user_id='local')
            db.add(row)
        connections = json.loads(row.ai_connections_json or '{}')
        connections['auto-roundtrip-qa'] = {'provider': 'compatible', 'label': 'Sandbox QA',
            'model': 'deepseek-chat', 'base_url': 'http://127.0.0.1:9655/v1'}
        row.ai_connections_json = json.dumps(connections)
        await db.commit()
    app = FastAPI()
    app.include_router(settings_api.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        profile = LocalizationProfile(id='auto-roundtrip-qa', name='Sandbox Auto', translation_automation='auto',
            translation_routes=[{'connection_id':'auto-roundtrip-qa'}], rewrite_routes=[{'connection_id':'auto-roundtrip-qa'}])
        saved = await client.post('/api/settings/localization-profiles', json=profile.model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        loaded = await client.get('/api/settings/localization-profiles')
        assert loaded.status_code == 200
        actual = next(p for p in loaded.json()['profiles'] if p['id'] == profile.id)
        assert actual['translation_automation'] == 'auto'
        assert loaded.json()['presets']['fast']['translation_automation'] == 'auto'
        invalid = await client.post('/api/settings/localization-profiles', json={**profile.model_dump(), 'translation_automation':'pass_everything'})
        assert invalid.status_code == 422
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UserSettings).where(UserSettings.user_id == 'local'))).scalar_one()
        persisted = next(p for p in json.loads(row.localization_profiles_json)['profiles'] if p['id'] == profile.id)
        assert persisted['translation_automation'] == 'auto'
