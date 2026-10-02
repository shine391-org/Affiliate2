"""Upload size and filesystem ownership regressions; no external services."""
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from backend.api import media


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "settings", SimpleNamespace(TMP_DIR=tmp_path))
    return tmp_path


def png():
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, format="PNG")
    return data.getvalue()


@pytest.mark.asyncio
async def test_exact_limit_upload_and_serve(root):
    content = png()
    content += b"\0" * (media.MAX_IMAGE_SIZE - len(content))
    upload = UploadFile(filename="image.png", file=io.BytesIO(content))
    result = await media.upload_media(upload)
    response = await media.serve_media(result["filename"])
    assert Path(response.path).parent == root / "media_uploads"
    assert Path(response.path).read_bytes() == content


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [1, 1024 * 1024 * 1024])
async def test_oversized_upload_reads_only_limit_plus_one_and_leaves_no_file(root, extra):
    class Stream:
        filename = "huge.png"
        read_sizes = []
        async def read(self, size=-1):
            assert 0 < size <= media.MAX_IMAGE_SIZE + 1
            self.read_sizes.append(size)
            return b"x" * min(size, media.MAX_IMAGE_SIZE + extra)
    stream = Stream()
    with pytest.raises(HTTPException) as error:
        await media.upload_media(stream)
    assert error.value.status_code == 413
    assert stream.read_sizes == [media.MAX_IMAGE_SIZE + 1]
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_invalid_image_bytes_leave_no_artifact(root):
    with pytest.raises(HTTPException) as error:
        await media.upload_media(UploadFile(filename="bad.png", file=io.BytesIO(b"not an image")))
    assert error.value.status_code == 400
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../private.png", "..\\private.png", "nested/image.png", "C:\\private.png", "C:private.png", "image.png:secret", "NUL", "image.png.", "image.png ", "missing.png", "directory", "private.png"])
async def test_serving_rejects_escape_non_files_and_shared_tmp(root, name):
    (root / "private.png").write_bytes(b"pipeline private artifact")
    uploads = root / "media_uploads"
    uploads.mkdir()
    (uploads / "directory").mkdir()
    with pytest.raises(HTTPException) as error:
        await media.serve_media(name)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_file_link_outside_namespace_is_rejected(root):
    private = root / "private.png"
    private.write_bytes(png())
    uploads = root / "media_uploads"
    uploads.mkdir()
    try:
        (uploads / "link.png").symlink_to(private)
    except OSError:
        pytest.skip("OS does not permit symlink creation for this account")
    with pytest.raises(HTTPException) as error:
        await media.serve_media("link.png")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_partial_write_failure_is_cleaned(root, monkeypatch):
    real_open = Path.open
    class FailedWrite:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            self.handle.close()
        def write(self, content):
            self.handle.write(content[:8])
            raise OSError("disk full")
    def fail_write(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return FailedWrite(handle) if mode == "xb" else handle
    monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises(HTTPException) as error:
        await media.upload_media(UploadFile(filename="image.png", file=io.BytesIO(png())))
    assert error.value.status_code == 503
    assert list((root / "media_uploads").iterdir()) == []
