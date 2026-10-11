# SPDX-License-Identifier: AGPL-3.0-only
"""Real filesystem/SQLite checks for interrupted local imports."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from backend.api import downloaded
from backend.database import AsyncSessionLocal, init_db
from backend.models.downloaded_video import DownloadedVideo


class Upload(UploadFile):
    def __init__(self, filename, parts):
        self.filename = filename
        self.parts = iter(parts)
        self.read_sizes = []

    async def read(self, size=-1):
        self.read_sizes.append(size)
        assert size == 1024 * 1024
        part = next(self.parts, b"")
        if isinstance(part, BaseException):
            raise part
        return part


@pytest.fixture
def import_dirs(monkeypatch, tmp_path):
    video = tmp_path / "videos"
    audio = tmp_path / "audio"
    video.mkdir()
    audio.mkdir()
    monkeypatch.setattr(downloaded, "settings", SimpleNamespace(
        VIDEOS_DIR=video, AUDIO_DIR=audio))
    monkeypatch.setattr(downloaded, "uuid4", lambda: "upload-fixed")
    return video, audio


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".mp4", ".wav"])
@pytest.mark.parametrize("failure", [OSError("disconnected"), asyncio.CancelledError()])
async def test_interrupted_import_preserves_previous_file_and_removes_partial(
    import_dirs, suffix, failure,
):
    video, audio = import_dirs
    folder = audio if suffix == ".wav" else video
    previous = folder / ("upload-fixed" + suffix)
    previous.write_bytes(b"last-known-good")
    upload = Upload("source" + suffix, [b"new-prefix", failure])
    with pytest.raises(type(failure)):
        await downloaded.import_local_video(upload, title="QA")
    assert previous.read_bytes() == b"last-known-good"
    assert list(folder.iterdir()) == [previous]
    assert len(upload.read_sizes) == 2


@pytest.mark.asyncio
async def test_empty_import_preserves_previous_file(import_dirs):
    video, _ = import_dirs
    previous = video / "upload-fixed.mp4"
    previous.write_bytes(b"last-known-good")
    with pytest.raises(HTTPException) as exc:
        await downloaded.import_local_video(Upload("source.mp4", []), title="QA")
    assert exc.value.status_code == 400
    assert previous.read_bytes() == b"last-known-good"
    assert list(video.iterdir()) == [previous]


@pytest.mark.asyncio
async def test_completed_import_streams_bytes_and_records_exact_path(
    import_dirs, monkeypatch,
):
    from backend.core import task_runner
    await init_db()
    async def no_analysis(**kwargs):
        return None
    dispatched = []
    def dispatch(coro):
        dispatched.append(coro)
        coro.close()
    monkeypatch.setattr(task_runner, "run_analyze_imported", no_analysis)
    monkeypatch.setattr(task_runner, "dispatch", dispatch)
    data = b"v" * (1024 * 1024) + b"tail"
    upload = Upload("source.mp4", [data[:1024 * 1024], data[1024 * 1024:]])
    response = await downloaded.import_local_video(upload, title="Atomic import QA")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(DownloadedVideo).where(
            DownloadedVideo.id == response["id"]))).scalar_one()
        assert Path(str(row.video_path)).read_bytes() == data
        assert Path(str(row.video_path)) == import_dirs[0] / "upload-fixed.mp4"
    assert len(upload.read_sizes) == 3
    assert len(dispatched) == 1
    assert not list(import_dirs[0].glob("*.partial"))
