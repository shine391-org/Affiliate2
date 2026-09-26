"""Canonical Meta publishing adapters for Facebook Page Reels and Instagram Reels.

Scope is intentionally narrow:
- Facebook: Page publishing only (not personal profiles)
- Instagram: Professional account (Business/Creator) linked to that Page
- One verified Page access token is reused for both destinations
- Local files are uploaded directly to Meta's upload endpoints; no third-party
  temporary file host is involved.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from backend.core.exceptions import UploadAuthError, UploadError

logger = logging.getLogger(__name__)

# Keep the version centralized so a future Meta version bump changes one place.
GRAPH_API_VERSION = "v24.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
RUPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload"


def _raise_graph_error(response: httpx.Response, action: str) -> None:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    err = payload.get("error") if isinstance(payload, dict) else None
    message = ""
    if isinstance(err, dict):
        message = str(err.get("message") or "")
    if not message:
        message = response.text[:500]
    status = response.status_code
    if status in {400, 401, 403}:
        raise UploadAuthError(f"{action} failed ({status}): {message}")
    raise UploadError(f"{action} failed ({status}): {message}")


async def inspect_meta_page(*, page_id: str, access_token: str) -> dict:
    """Validate a Page token and discover a linked Instagram professional account."""
    page_id = str(page_id or "").strip()
    access_token = str(access_token or "").strip()
    if not page_id or not access_token:
        raise UploadAuthError("Meta Page ID and Page access token are required.")

    fields = "id,name,instagram_business_account"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(
            f"{GRAPH_API_BASE}/{page_id}",
            params={"fields": fields, "access_token": access_token},
        )
    if response.status_code != 200:
        _raise_graph_error(response, "Meta Page verification")
    payload = response.json()
    ig = payload.get("instagram_business_account") or {}
    return {
        "ok": True,
        "page_id": str(payload.get("id") or page_id),
        "page_name": str(payload.get("name") or ""),
        "instagram_user_id": str(ig.get("id") or "") or None,
        "facebook_ready": True,
        "instagram_ready": bool(ig.get("id")),
    }


async def _upload_binary(
    upload_url: str,
    *,
    file_path: Path,
    access_token: str,
) -> None:
    size = file_path.stat().st_size
    headers = {
        "Authorization": f"OAuth {access_token}",
        "offset": "0",
        "file_size": str(size),
        "Content-Type": "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        with file_path.open("rb") as handle:
            response = await client.post(
                upload_url,
                headers=headers,
                content=handle,
            )
    if response.status_code not in {200, 201}:
        _raise_graph_error(response, "Meta binary upload")


async def upload_facebook_reel(
    *,
    video_path: str,
    description: str,
    page_id: str,
    access_token: str,
) -> dict:
    """Publish one Reel to a Facebook Page from a local file."""
    path = Path(video_path)
    if not path.is_file():
        raise UploadError(f"Video file not found: {video_path}")
    if not page_id or not access_token:
        raise UploadAuthError("Facebook Page publish is not configured.")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        start = await client.post(
            f"{GRAPH_API_BASE}/{page_id}/video_reels",
            params={
                "upload_phase": "start",
                "access_token": access_token,
            },
        )
        if start.status_code != 200:
            _raise_graph_error(start, "Facebook Reel upload start")
        start_payload = start.json()
        video_id = str(start_payload.get("video_id") or "")
        upload_url = str(start_payload.get("upload_url") or "")
        if not video_id or not upload_url:
            raise UploadError("Facebook did not return video_id/upload_url.")

    await _upload_binary(upload_url, file_path=path, access_token=access_token)

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        finish = await client.post(
            f"{GRAPH_API_BASE}/{page_id}/video_reels",
            params={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description or "",
                "access_token": access_token,
            },
        )
        if finish.status_code != 200:
            _raise_graph_error(finish, "Facebook Reel publish")

        permalink = ""
        detail = await client.get(
            f"{GRAPH_API_BASE}/{video_id}",
            params={
                "fields": "permalink_url",
                "access_token": access_token,
            },
        )
        if detail.status_code == 200:
            permalink = str(detail.json().get("permalink_url") or "")

    return {
        "video_id": video_id,
        "url": permalink,
    }


async def _wait_instagram_container(
    *,
    container_id: str,
    access_token: str,
    timeout_seconds: int = 180,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{GRAPH_API_BASE}/{container_id}",
                params={
                    "fields": "status_code,status",
                    "access_token": access_token,
                },
            )
        if response.status_code != 200:
            _raise_graph_error(response, "Instagram container status")
        payload = response.json()
        status = str(payload.get("status_code") or "").upper()
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise UploadError(
                "Instagram media processing failed: "
                + str(payload.get("status") or status)
            )
        await asyncio.sleep(4)
    raise UploadError("Instagram media processing timed out.")


async def upload_instagram_reel(
    *,
    video_path: str,
    caption: str,
    ig_user_id: str,
    access_token: str,
) -> dict:
    """Publish one Reel to a linked Instagram Professional account."""
    path = Path(video_path)
    if not path.is_file():
        raise UploadError(f"Video file not found: {video_path}")
    if not ig_user_id or not access_token:
        raise UploadAuthError(
            "Instagram publish needs a linked Professional account and Meta Page token."
        )

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        create = await client.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media",
            params={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption or "",
                "access_token": access_token,
            },
        )
        if create.status_code != 200:
            _raise_graph_error(create, "Instagram Reel container creation")
        payload = create.json()
        container_id = str(payload.get("id") or "")
        upload_uri = str(payload.get("uri") or "")
        if not container_id:
            raise UploadError("Instagram did not return a media container ID.")
        if not upload_uri:
            upload_uri = f"{RUPLOAD_BASE}/{GRAPH_API_VERSION}/{container_id}"

    await _upload_binary(
        upload_uri,
        file_path=path,
        access_token=access_token,
    )
    await _wait_instagram_container(
        container_id=container_id,
        access_token=access_token,
    )

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        publish = await client.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": access_token,
            },
        )
        if publish.status_code != 200:
            _raise_graph_error(publish, "Instagram Reel publish")
        media_id = str(publish.json().get("id") or "")
        if not media_id:
            raise UploadError("Instagram publish returned no media ID.")

        detail = await client.get(
            f"{GRAPH_API_BASE}/{media_id}",
            params={"fields": "permalink", "access_token": access_token},
        )
        permalink = ""
        if detail.status_code == 200:
            permalink = str(detail.json().get("permalink") or "")

    return {
        "media_id": media_id,
        "permalink": permalink,
        "container_id": container_id,
    }
