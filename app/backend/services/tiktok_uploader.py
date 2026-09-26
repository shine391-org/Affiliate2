# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""TikTok Content Posting API adapter.

Canonical Publish uses TikTok's official OAuth Direct Post flow. The legacy
cookie uploader is intentionally not accepted here because it cannot return a
provider-verifiable publication result and previously fabricated a local
"publish_id", which could make the app report a post that TikTok never confirmed.
"""
import asyncio
import logging
from pathlib import Path

import httpx

from backend.config import settings
from backend.core.exceptions import UploadError, UploadAuthError, UploadOutcomeUncertainError

logger = logging.getLogger(__name__)

TIKTOK_API = "https://open.tiktokapis.com/v2"
CHUNK_SIZE = 10_000_000  # 10 MB
_DIRECT_POST_INIT = "/post/publish/video/init/"
_CREATOR_INFO = "/post/publish/creator_info/query/"
_STATUS = "/post/publish/status/fetch/"


def _api_error(resp: httpx.Response, fallback: str) -> tuple[str, str]:
    try:
        payload = resp.json()
    except Exception:
        return "", (resp.text or fallback)[:300]
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "", fallback
    code = str(error.get("code") or "")
    message = str(error.get("message") or fallback)
    return code, message[:300]


def _raise_api_error(resp: httpx.Response, context: str) -> None:
    code, message = _api_error(resp, context)
    if resp.status_code == 401 or code in {"access_token_invalid", "scope_not_authorized"}:
        raise UploadAuthError(f"TikTok authorization failed ({code or resp.status_code}): {message}")
    raise UploadError(f"{context} ({code or resp.status_code}): {message}")


async def _creator_info_with_client(
    client: httpx.AsyncClient,
    access_token: str,
) -> dict:
    resp = await client.post(
        f"{TIKTOK_API}{_CREATOR_INFO}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={},
    )
    code, _ = _api_error(resp, "TikTok creator info failed")
    if resp.status_code != 200 or (code and code != "ok"):
        _raise_api_error(resp, "TikTok creator info failed")
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise UploadError("TikTok creator info returned no data")
    return data


async def get_tiktok_creator_info(access_token: str) -> dict:
    """Return the latest Direct Post capabilities for the OAuth creator."""
    if not access_token:
        raise UploadAuthError("TikTok OAuth access token is required.")
    async with httpx.AsyncClient(timeout=30) as client:
        return await _creator_info_with_client(client, access_token)


async def upload_to_tiktok(
    video_path: str,
    title: str,
    access_token: str = "",
    privacy: str = "PUBLIC_TO_EVERYONE",
    cookie_sessionid: str = "",
    *,
    disable_duet: bool = False,
    disable_comment: bool = False,
    disable_stitch: bool = False,
) -> dict:
    """Direct-post one local video through TikTok's official OAuth API."""
    path = Path(video_path)
    if not path.exists():
        raise UploadError(f"Video file not found: {video_path}")

    if not access_token:
        if cookie_sessionid:
            raise UploadAuthError(
                "TikTok is only connected through the legacy cookie uploader. "
                "Canonical Publish requires TikTok OAuth so Affiliate2 can verify "
                "the Direct Post result. Reconnect TikTok from Publish/Settings."
            )
        raise UploadAuthError(
            "TikTok OAuth is not connected. Connect TikTok before publishing."
        )

    return await _upload_via_api(
        path,
        title,
        access_token,
        privacy,
        disable_duet=disable_duet,
        disable_comment=disable_comment,
        disable_stitch=disable_stitch,
    )


async def _upload_via_api(
    video_path: Path,
    title: str,
    access_token: str,
    privacy: str,
    *,
    disable_duet: bool = False,
    disable_comment: bool = False,
    disable_stitch: bool = False,
) -> dict:
    """Direct Post using TikTok's Content Posting API."""
    file_size = video_path.stat().st_size
    if file_size <= 0:
        raise UploadError("TikTok video file is empty")
    # TikTok's media-transfer contract defines total_chunk_count as floor(
    # video_size / chunk_size). The final chunk absorbs trailing bytes and may
    # therefore be larger than chunk_size. For a one-chunk file, advertise the
    # whole file as chunk_size.
    chunk_size = min(CHUNK_SIZE, file_size)
    total_chunks = max(1, file_size // chunk_size)

    async with httpx.AsyncClient(timeout=120) as client:
        # TikTok requires current creator capabilities to drive privacy and
        # interaction settings on the export surface before Direct Post.
        creator = await _creator_info_with_client(client, access_token)
        privacy_options = list(creator.get("privacy_level_options") or [])
        if privacy not in privacy_options:
            allowed = ", ".join(privacy_options) or "none"
            raise UploadError(
                f"TikTok privacy '{privacy}' is not available for this creator. "
                f"Available options: {allowed}."
            )

        # Creator-level restrictions are authoritative: a UI preference can
        # disable an interaction, but it can never re-enable one TikTok reports
        # as disabled for the creator.
        disable_duet = bool(disable_duet or creator.get("duet_disabled"))
        disable_comment = bool(disable_comment or creator.get("comment_disabled"))
        disable_stitch = bool(disable_stitch or creator.get("stitch_disabled"))

        init_resp = await client.post(
            f"{TIKTOK_API}{_DIRECT_POST_INIT}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": title[:2200],
                    "privacy_level": privacy,
                    "disable_duet": disable_duet,
                    "disable_comment": disable_comment,
                    "disable_stitch": disable_stitch,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunks,
                },
            },
        )
        code, _ = _api_error(init_resp, "TikTok Direct Post init failed")
        if init_resp.status_code != 200 or (code and code != "ok"):
            _raise_api_error(init_resp, "TikTok Direct Post init failed")

        init_data = init_resp.json().get("data", {})
        publish_id = init_data.get("publish_id")
        upload_url = init_data.get("upload_url")
        if not publish_id or not upload_url:
            raise UploadError("TikTok Direct Post init returned no publish_id or upload_url")

        with open(video_path, "rb") as handle:
            for chunk_idx in range(total_chunks):
                # The final chunk takes all remaining bytes, as required by
                # TikTok when video_size is not an exact multiple.
                read_size = (
                    chunk_size
                    if chunk_idx < total_chunks - 1
                    else file_size - (chunk_idx * chunk_size)
                )
                chunk_data = handle.read(read_size)
                if not chunk_data:
                    raise UploadError(
                        f"TikTok source ended early at chunk {chunk_idx + 1}"
                    )
                start = chunk_idx * chunk_size
                end = start + len(chunk_data) - 1
                chunk_resp = await client.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Length": str(len(chunk_data)),
                        "Content-Type": "video/mp4",
                    },
                    content=chunk_data,
                )
                if chunk_resp.status_code not in (200, 201, 206):
                    raise UploadError(
                        f"TikTok chunk upload failed at chunk {chunk_idx + 1}/"
                        f"{total_chunks}: HTTP {chunk_resp.status_code}"
                    )
                logger.info("TikTok upload chunk %d/%d", chunk_idx + 1, total_chunks)

        # Once every byte has been accepted, any transport/parsing/status
        # ambiguity is potentially side-effectful: TikTok may still finish the
        # post. Keep polling through transient errors, but never downgrade the
        # final result to ordinary FAILED or discard the provider publish_id.
        last_status_error = ""
        for _ in range(60):  # up to ~2 minutes
            await asyncio.sleep(2)
            try:
                status_resp = await client.post(
                    f"{TIKTOK_API}{_STATUS}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    json={"publish_id": publish_id},
                )
            except Exception as exc:
                last_status_error = f"transport error: {exc}"
                logger.warning("TikTok status poll transport error; retrying: %s", exc)
                continue

            if status_resp.status_code >= 500:
                last_status_error = f"HTTP {status_resp.status_code}"
                logger.warning(
                    "TikTok status poll returned HTTP %s; retrying",
                    status_resp.status_code,
                )
                continue

            code, status_error = _api_error(
                status_resp, "TikTok post status failed"
            )
            if status_resp.status_code != 200 or (code and code != "ok"):
                raise UploadOutcomeUncertainError(
                    "TikTok accepted the upload, but the final status endpoint "
                    f"could not confirm the result ({code or status_resp.status_code}): "
                    f"{status_error}. Check TikTok before retrying.",
                    provider_result={"publish_id": publish_id},
                )

            try:
                status_data = status_resp.json().get("data", {})
            except Exception as exc:
                last_status_error = f"malformed status response: {exc}"
                logger.warning("TikTok status response was not valid JSON; retrying")
                continue

            if not isinstance(status_data, dict):
                last_status_error = "status response data was not an object"
                continue

            status = status_data.get("status")
            if status == "PUBLISH_COMPLETE":
                public_ids = status_data.get("publicaly_available_post_id") or []
                post_id = str(public_ids[0]) if public_ids else None
                logger.info("TikTok Direct Post complete: %s", publish_id)
                return {
                    "publish_id": publish_id,
                    "post_id": post_id,
                    "privacy_level": privacy,
                    "creator_username": creator.get("creator_username"),
                }
            if status == "FAILED":
                raise UploadError(
                    f"TikTok publish failed: {status_data.get('fail_reason') or 'Unknown'}"
                )

        suffix = f" Last status error: {last_status_error}." if last_status_error else ""
        raise UploadOutcomeUncertainError(
            "TikTok publish timed out without provider confirmation. TikTok may "
            "still be processing the post; check TikTok before retrying to avoid "
            f"a duplicate post.{suffix}",
            provider_result={"publish_id": publish_id},
        )


def build_tiktok_auth_url() -> str:
    """Build TikTok OAuth authorization URL for official Direct Post."""
    import urllib.parse

    params = {
        "client_key": settings.TIKTOK_CLIENT_KEY,
        "scope": "video.publish,video.list",
        "response_type": "code",
        "redirect_uri": settings.TIKTOK_REDIRECT_URI,
        "state": "affiliate2_tiktok_publish_auth",
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urllib.parse.urlencode(params)}"


async def exchange_tiktok_code(code: str) -> dict:
    """Exchange TikTok OAuth code for access + refresh tokens."""
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
