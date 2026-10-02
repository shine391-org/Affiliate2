"""Binary upload failure boundaries, with real streams and controlled HTTP I/O."""
import asyncio
from pathlib import Path

import httpx
import pytest

from backend.core.exceptions import UploadError
from backend.services import meta_uploader as meta


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ConnectError, httpx.WriteTimeout, httpx.ReadTimeout, asyncio.CancelledError, None])
async def test_upload_finite_timeouts_and_resource_cleanup(tmp_path, monkeypatch, failure):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video data")
    handles = []
    real_open = Path.open
    def track_open(file_path, *args, **kwargs):
        handle = real_open(file_path, *args, **kwargs)
        handles.append(handle)
        return handle
    monkeypatch.setattr(Path, "open", track_open)
    clients = []
    class Client:
        def __init__(self, *, timeout, **kwargs):
            for phase in (timeout.connect, timeout.pool, timeout.read, timeout.write):
                assert 0 < phase < float("inf")
            assert timeout.write >= 120
            self.closed = False
            self.posts = 0
            clients.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            self.closed = True
        async def post(self, url, *, content, **kwargs):
            self.posts += 1
            if failure in (httpx.ConnectTimeout, httpx.ConnectError):
                raise failure("connect failed")
            assert await anext(content) == b"video data"
            if failure:
                raise failure("interrupted after consuming a chunk")
            return httpx.Response(200)
    monkeypatch.setattr(meta.httpx, "AsyncClient", Client)
    operation = meta._upload_binary("https://upload.invalid", file_path=path, access_token="test-only")
    if failure is None:
        await asyncio.wait_for(operation, 2)
    else:
        expected = asyncio.CancelledError if failure is asyncio.CancelledError else UploadError
        with pytest.raises(expected) as error:
            await asyncio.wait_for(operation, 2)
        if expected is UploadError:
            assert "before retrying" in str(error.value)
    assert len(clients) == 1 and clients[0].posts == 1
    assert clients[0].closed
    assert all(handle.closed for handle in handles)
    if failure not in (httpx.ConnectTimeout, httpx.ConnectError):
        assert len(handles) == 1
