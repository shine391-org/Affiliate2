# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
TikTok upload service.
Primary: Official TikTok API (OAuth)
Fallback: tiktok-uploader via cookies
"""
import asyncio
import json
import logging
import math
from pathlib import Path

import httpx

from backend.config import settings
from backend.core.exceptions import UploadError, UploadAuthError

logger = logging.getLogger(__name__)

TIKTOK_API = "https://open.tiktokapis.com/v2"
CHUNK_SIZE = 10_000_000  # 10MB per chunk


async def upload_to_tiktok(
    video_path: str,
    title: str,
    access_token: str = "",
    privacy: str = "PUBLIC_TO_EVERYONE",
    cookie_sessionid: str = "",
) -> dict:
    """
    Upload a video to TikTok.
    Tries OAuth API first, falls back to cookie-based upload.
    Returns {"publish_id": "..."}.
    """
    path = Path(video_path)
    if not path.exists():
        raise UploadError(f"Video file not found: {video_path}")

    if access_token:
        return await _upload_via_api(path, title, access_token, privacy)
    elif cookie_sessionid:
        return await _upload_via_cookies(path, title, cookie_sessionid)
    else:
        raise UploadAuthError(
            "TikTok upload requires either OAuth access token or session cookie. "
            "Connect your TikTok account in Settings."
        )


async def _upload_via_api(
    video_path: Path,
    title: str,
    access_token: str,
    privacy: str,
) -> dict:
    """Upload using TikTok's official Content Posting API."""
    file_size = video_path.stat().st_size
    total_chunks = math.ceil(file_size / CHUNK_SIZE)

    async with httpx.AsyncClient(timeout=120) as client:
        # Step 1: Initialize upload
        init_resp = await client.post(
            f"{TIKTOK_API}/post/publish/inbox/video/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": title[:150],
                    "privacy_level": privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": CHUNK_SIZE,
                    "total_chunk_count": total_chunks,
                },
            },
        )

        if init_resp.status_code != 200:
            error_data = init_resp.json() if init_resp.headers.get("content-type", "").startswith("application/json") else {}
            error_msg = error_data.get("error", {}).get("message", init_resp.text[:300])
            if "access_token" in error_msg.lower() or init_resp.status_code == 401:
                raise UploadAuthError(f"TikTok OAuth token invalid or expired: {error_msg}")
            raise UploadError(f"TikTok upload init failed: {error_msg}")

        init_data = init_resp.json().get("data", {})
        publish_id = init_data.get("publish_id")
        upload_url = init_data.get("upload_url")

        if not publish_id or not upload_url:
            raise UploadError("TikTok upload init returned no publish_id or upload_url")

        # Step 2: Upload chunks
        with open(video_path, "rb") as f:
            for chunk_idx in range(total_chunks):
                chunk_data = f.read(CHUNK_SIZE)
                start = chunk_idx * CHUNK_SIZE
                end = start + len(chunk_data) - 1

                chunk_resp = await client.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Type": "video/mp4",
                    },
                    content=chunk_data,
                )

                if chunk_resp.status_code not in (200, 201, 206):
                    raise UploadError(f"TikTok chunk upload failed at chunk {chunk_idx}: {chunk_resp.status_code}")

                logger.info(f"TikTok upload chunk {chunk_idx + 1}/{total_chunks}")

        # Step 3: Poll for completion
        for _ in range(30):  # up to 60 seconds
            await asyncio.sleep(2)
            status_resp = await client.post(
                f"{TIKTOK_API}/post/publish/status/fetch/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"publish_id": publish_id},
            )

            if status_resp.status_code == 200:
                status_data = status_resp.json().get("data", {})
                status = status_data.get("status")
                if status == "PUBLISH_COMPLETE":
                    logger.info(f"TikTok upload complete: {publish_id}")
                    return {"publish_id": publish_id}
                elif status in ("FAILED", "PUBLISH_FAILED"):
                    fail_reason = status_data.get("fail_reason", "Unknown")
                    raise UploadError(f"TikTok publish failed: {fail_reason}")

        raise UploadError("TikTok upload timed out waiting for publish confirmation")


async def _upload_via_cookies(
    video_path: Path,
    title: str,
    sessionid: str,
) -> dict:
    """Fallback: upload using tiktok-uploader library with session cookie."""
    cookie_path = settings.TMP_DIR / "tiktok_cookies.txt"
    cookie_path.parent.mkdir(parents=True, exist_ok=True)

    cookie_content = (
        "# Netscape HTTP Cookie File\n"
        f".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\t{sessionid}\n"
    )

    def _upload():
        cookie_path.write_text(cookie_content)
        try:
            from tiktok_uploader.upload import upload_video as ttu_upload
            result = ttu_upload(
                filename=str(video_path),
                description=title[:150],
                cookies=str(cookie_path),
            )
            return {"publish_id": f"cookie_upload_{hash(str(video_path)) & 0xFFFFFFFF:08x}"}
        finally:
            cookie_path.unlink(missing_ok=True)

    try:
        return await asyncio.to_thread(_upload)
    except Exception as e:
        raise UploadError(f"TikTok cookie upload failed: {e}")


def build_tiktok_auth_url() -> str:
    """Build TikTok OAuth authorization URL."""
    import urllib.parse

    params = {
        "client_key": settings.TIKTOK_CLIENT_KEY,
        "scope": "video.upload,video.publish,video.list",
        "response_type": "code",
        "redirect_uri": settings.TIKTOK_REDIRECT_URI,
        "state": "viralmint_tiktok_auth",
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urllib.parse.urlencode(params)}"


async def exchange_tiktok_code(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TIKTOK_API}/oauth/token/",
            data={
                "client_key": settings.TIKTOK_CLIENT_KEY,
                "client_secret": settings.TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.TIKTOK_REDIRECT_URI,
            },
        )

    if resp.status_code != 200:
        raise UploadAuthError(f"TikTok token exchange failed: {resp.text[:300]}")

    data = resp.json()
    if "access_token" not in data:
        raise UploadAuthError(f"TikTok token exchange returned no access_token: {data}")

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_in": data.get("expires_in", 86400),
    }
