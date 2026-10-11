# SPDX-License-Identifier: AGPL-3.0-only
"""Real poster endpoint, SQLite, FFmpeg and JPEG cache integration."""
import json
import subprocess
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from backend.config import Settings
from backend.database import AsyncSessionLocal, get_db, init_db
from backend.models.job import Job
from backend.services import library_index
from backend.api import library
from backend.services import ffmpeg_service


@pytest.mark.asyncio
async def test_poster_cache_never_scans_library_or_bypasses_current_eligibility(monkeypatch, tmp_path):
    await init_db()
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: tmp_path))
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:d=4", "-c:v", "libx264", str(source)],
                   check=True, capture_output=True, timeout=30)

    async def forbidden(*args, **kwargs):
        pytest.fail("poster request must not scan the Library")

    monkeypatch.setattr(library_index, "gather", forbidden)
    app = FastAPI()
    app.include_router(library.router, prefix="/api")
    job_id = str(uuid4())
    async with AsyncSessionLocal() as db:
        row = Job(id=job_id, job_type="tool:translate", status="success",
                  output_json=json.dumps({"file": str(source)}))
        db.add(row)
        await db.commit()

        async def session():
            yield db

        app.dependency_overrides[get_db] = session
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                url = f"/api/library/poster?key=job:{job_id}"
                first = await client.get(url)
                assert first.status_code == 200, first.text
                assert first.headers["content-type"] == "image/jpeg"
                assert first.content.startswith(b"\xff\xd8")
                # The second request must use the real JPEG already on disk.
                monkeypatch.setattr(ffmpeg_service, "extract_thumbnail", forbidden)
                assert (await client.get(url)).content == first.content
                row.status = "running"
                await db.commit()
                assert (await client.get(url)).status_code == 404
                row.status = "failed"  # last-good localization remains reachable
                await db.commit()
                assert (await client.get(url)).status_code == 200
                source.unlink()
                assert (await client.get(url)).status_code == 404
                await db.delete(row)
                await db.commit()
                assert (await client.get(url)).status_code == 404
        finally:
            existing = await db.get(Job, job_id)
            if existing is not None:
                await db.delete(existing)
                await db.commit()
