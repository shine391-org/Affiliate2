# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/media — simple image upload + serve for video generation inputs."""
import io
import logging
import stat
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from PIL import Image, UnidentifiedImageError

from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_IMAGE_SIZE = 20 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _is_redirect(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _upload_root(*, create: bool = False) -> Path:
    root = settings.TMP_DIR / "media_uploads"
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir() or _is_redirect(root):
        raise HTTPException(404, "Media upload directory unavailable")
    return root.resolve()


@router.post("/media/upload")
async def upload_media(file: UploadFile = File(...)):
    """Upload an image for use as video generation input (I2V, start/end frame)."""
    suffix = Path(file.filename).suffix.lower() if file.filename else ""
    if suffix not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, f"Unsupported image type: {suffix}. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTS))}")

    content = await file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(413, f"Image too large ({len(content) // (1024*1024)}MB). Maximum: 20MB")

    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise ValueError("Unsupported image data")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "Upload must contain a valid supported image.") from exc

    file_id = str(uuid4())[:12]
    filename = f"{file_id}{suffix}"
    dest = _upload_root(create=True) / filename
    try:
        # Exclusive creation never follows an existing link or overwrites a file.
        with dest.open("xb") as handle:
            try:
                handle.write(content)
            except BaseException:
                handle.close()
                dest.unlink(missing_ok=True)
                raise
    except OSError as exc:
        raise HTTPException(503, "Could not store image; check disk space and permissions.") from exc

    size_kb = len(content) / 1024
    logger.info(f"Media uploaded: {filename} ({size_kb:.0f}KB)")

    return {
        "id": file_id,
        "filename": filename,
        "url": f"/api/media/{filename}",
        "size_kb": round(size_kb, 1),
    }


@router.get("/media/{filename}")
async def serve_media(filename: str):
    """Serve an uploaded media file."""
    # Reject both POSIX and Windows traversal, drive paths and alternate streams.
    windows_name = PureWindowsPath(filename)
    if (not filename or filename in {".", ".."} or "/" in filename
            or "\\" in filename or ":" in filename or windows_name.drive
            or windows_name.is_reserved() or filename.endswith((".", " "))):
        raise HTTPException(404, "File not found")
    root = _upload_root()
    path = root / filename
    try:
        if _is_redirect(path) or path.resolve().parent != root or not path.is_file():
            raise HTTPException(404, "File not found")
    except OSError as exc:
        raise HTTPException(404, "File not found") from exc
    return FileResponse(path)
