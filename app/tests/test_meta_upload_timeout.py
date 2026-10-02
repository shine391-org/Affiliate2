"""Binary upload failure boundaries, with real streams and controlled HTTP I/O."""
import asyncio
from pathlib import Path

import httpx
import pytest

from backend.core.exceptions import UploadError, UploadOutcomeUncertainError
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
    operation = meta._upload_binary(
        "https://upload.invalid",
        file_path=path,
        access_token="test-only",
        provider_result={"video_id": "vid-123"},
    )
    if failure is None:
        await asyncio.wait_for(operation, 2)
    else:
        expected = asyncio.CancelledError if failure is asyncio.CancelledError else UploadOutcomeUncertainError
        with pytest.raises(expected) as error:
            await asyncio.wait_for(operation, 2)
        if expected is UploadOutcomeUncertainError:
            assert "verify the destination before retrying" in str(error.value)
            assert error.value.provider_result == {"video_id": "vid-123"}
    assert len(clients) == 1 and clients[0].posts == 1
    assert clients[0].closed
    assert all(handle.closed for handle in handles)
    if failure not in (httpx.ConnectTimeout, httpx.ConnectError):
        assert len(handles) == 1


@pytest.mark.asyncio
async def test_facebook_publish_confirmation_timeout_is_uncertain(tmp_path, monkeypatch):
    from backend.core.exceptions import UploadOutcomeUncertainError

    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async def uploaded(*_args, **_kwargs):
        return None
    monkeypatch.setattr(meta, "_upload_binary", uploaded)

    instances = []
    class Client:
        def __init__(self, **_kwargs):
            self.index = len(instances)
            instances.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def post(self, *_args, **_kwargs):
            if self.index == 0:
                return httpx.Response(200, json={"video_id": "fb-123", "upload_url": "https://upload.invalid"})
            raise httpx.ReadTimeout("finish response lost")

    monkeypatch.setattr(meta.httpx, "AsyncClient", Client)
    with pytest.raises(UploadOutcomeUncertainError) as error:
        await meta.upload_facebook_reel(
            video_path=str(path), description="test", page_id="page", access_token="token",
        )
    assert error.value.provider_result == {"video_id": "fb-123"}


@pytest.mark.asyncio
async def test_instagram_publish_confirmation_timeout_is_uncertain(tmp_path, monkeypatch):
    from backend.core.exceptions import UploadOutcomeUncertainError

    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async def uploaded(*_args, **_kwargs):
        return None
    async def processed(**_kwargs):
        return None
    monkeypatch.setattr(meta, "_upload_binary", uploaded)
    monkeypatch.setattr(meta, "_wait_instagram_container", processed)

    instances = []
    class Client:
        def __init__(self, **_kwargs):
            self.index = len(instances)
            instances.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def post(self, *_args, **_kwargs):
            if self.index == 0:
                return httpx.Response(200, json={"id": "ig-container", "uri": "https://upload.invalid"})
            raise httpx.ReadTimeout("publish response lost")

    monkeypatch.setattr(meta.httpx, "AsyncClient", Client)
    with pytest.raises(UploadOutcomeUncertainError) as error:
        await meta.upload_instagram_reel(
            video_path=str(path), caption="test", ig_user_id="ig-user", access_token="token",
        )
    assert error.value.provider_result == {"container_id": "ig-container"}


@pytest.mark.asyncio
async def test_facebook_permalink_lookup_failure_keeps_success(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    async def uploaded(*_args, **_kwargs):
        return None
    monkeypatch.setattr(meta, "_upload_binary", uploaded)

    instances = []
    class Client:
        def __init__(self, **_kwargs):
            self.index = len(instances)
            instances.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def post(self, *_args, **_kwargs):
            if self.index == 0:
                return httpx.Response(200, json={"video_id": "fb-123", "upload_url": "https://upload.invalid"})
            return httpx.Response(200, json={"success": True})
        async def get(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("permalink lookup timed out")

    monkeypatch.setattr(meta.httpx, "AsyncClient", Client)
    result = await meta.upload_facebook_reel(
        video_path=str(path), description="test", page_id="page", access_token="token",
    )
    assert result == {"video_id": "fb-123", "url": ""}
