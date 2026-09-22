"""Phase 3.5 publish adapters.

Human approval is the hard gate. Facebook/YouTube publishing is user-triggered.
TikTok remains manual-only: the backend never uploads to TikTok.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .publish_store import PublishStore, PublishValidationError
from .review_store import ReviewStore


YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_PRIVACY = {"private", "unlisted", "public"}


class PublishActionError(ValueError):
    pass


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _approved_detail(review_store: ReviewStore, source_platform: str, content_id: str) -> dict:
    detail = review_store.get_candidate_detail(source_platform, content_id)
    if not detail.get("remake_exists"):
        raise PublishActionError("No final video exists for this candidate.")
    review = detail.get("remake_review") or {}
    if review.get("status") != "APPROVED":
        raise PublishActionError("Final video must be APPROVED before publishing.")
    final_path = (
        review_store.root
        / "data"
        / "remakes"
        / f"{source_platform}_{content_id}"
        / "final.mp4"
    )
    if not final_path.is_file():
        raise PublishActionError("Approved final.mp4 is missing.")
    detail["_final_path"] = str(final_path)
    return detail


def default_publish_metadata(detail: dict) -> dict[str, str]:
    adaptation = detail.get("adaptation") or {}
    va = adaptation.get("vietnamese_adaptation") or {}
    translation = detail.get("translation") or {}

    title = (va.get("hook") or "").strip()
    caption = (va.get("script") or "").strip()

    if not title:
        segments = translation.get("segments") or []
        if segments:
            title = str(segments[0].get("text") or "").strip()
            caption = " ".join(
                str(s.get("text") or "").strip() for s in segments if s.get("text")
            ).strip()

    if not title:
        title = str(detail.get("title") or "").strip()
    if not caption:
        caption = title

    # UI-editable suggestions only; never fabricate hashtags or claims.
    title = title[:100]
    caption = caption[:5000]
    return {"title": title, "caption": caption}


def probe_video(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if res.returncode != 0:
        raise PublishActionError("Could not inspect final video with ffprobe.")
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise PublishActionError("ffprobe returned invalid output.") from exc
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float(fmt.get("duration") or 0),
        "frame_rate": stream.get("r_frame_rate"),
        "size": path.stat().st_size,
    }


def facebook_reel_eligibility(path: Path) -> tuple[bool, str | None, dict[str, Any]]:
    info = probe_video(path)
    w, h, duration = info["width"], info["height"], info["duration"]
    if w < 540 or h < 960:
        return False, "Facebook Reel requires at least 540x960.", info
    if h <= 0 or abs((w / h) - (9 / 16)) > 0.02:
        return False, "Facebook Reel requires a 9:16 final video.", info
    if duration < 4 or duration > 60.5:
        return False, "Facebook Reel duration must be about 4–60 seconds.", info
    return True, None, info


def facebook_configured() -> bool:
    return bool(_env("FACEBOOK_PAGE_ID") and _env("FACEBOOK_PAGE_ACCESS_TOKEN"))


def youtube_oauth_configured() -> bool:
    return bool(_env("YOUTUBE_CLIENT_ID") and _env("YOUTUBE_CLIENT_SECRET"))


def _youtube_client_config() -> dict:
    client_id = _env("YOUTUBE_CLIENT_ID")
    client_secret = _env("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise PublishActionError(
            "Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET before connecting YouTube."
        )
    redirect_uri = _env("YOUTUBE_REDIRECT_URI") or (
        "http://localhost:16888/api/publish/youtube/callback"
    )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def youtube_authorization_url(store: PublishStore) -> str:
    from google_auth_oauthlib.flow import Flow

    config = _youtube_client_config()
    redirect_uri = config["web"]["redirect_uris"][0]
    state = store.create_oauth_state("youtube")
    flow = Flow.from_client_config(
        config,
        scopes=[YOUTUBE_SCOPE],
        state=state,
        redirect_uri=redirect_uri,
    )
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return url


def youtube_exchange_code(store: PublishStore, state: str, code: str) -> None:
    if not store.consume_oauth_state(state):
        raise PublishActionError("OAuth state is invalid or expired.")

    from google_auth_oauthlib.flow import Flow

    config = _youtube_client_config()
    redirect_uri = config["web"]["redirect_uris"][0]
    flow = Flow.from_client_config(
        config,
        scopes=[YOUTUBE_SCOPE],
        state=state,
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    store.set_account(
        "youtube",
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or [YOUTUBE_SCOPE]),
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        },
    )


def _youtube_credentials(store: PublishStore):
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials

    data = store.get_account("youtube")
    if not data:
        raise PublishActionError("YouTube is not connected.")

    expiry = data.get("expiry")
    expiry_dt = None
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(expiry)
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            expiry_dt = None

    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=data.get("client_id") or _env("YOUTUBE_CLIENT_ID"),
        client_secret=data.get("client_secret") or _env("YOUTUBE_CLIENT_SECRET"),
        scopes=data.get("scopes") or [YOUTUBE_SCOPE],
        expiry=expiry_dt,
    )
    if not creds.valid:
        if not creds.refresh_token:
            raise PublishActionError("YouTube authorization expired; reconnect YouTube.")
        creds.refresh(GoogleRequest())
        store.set_account(
            "youtube",
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or [YOUTUBE_SCOPE]),
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            },
        )
    return creds


def _upload_youtube_sync(
    store: PublishStore,
    path: Path,
    *,
    title: str,
    description: str,
    privacy: str,
    made_for_kids: bool,
    progress_cb,
) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = _youtube_credentials(store)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    media = MediaFileUpload(
        str(path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": bool(made_for_kids),
            },
        },
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status is not None:
            progress_cb(float(status.progress()) * 100.0)

    video_id = response.get("id")
    if not video_id:
        raise PublishActionError("YouTube upload completed without a video id.")
    return {
        "external_id": video_id,
        "external_url": f"https://youtu.be/{video_id}",
    }


def _publish_facebook_sync(
    path: Path,
    *,
    title: str,
    description: str,
) -> dict:
    page_id = _env("FACEBOOK_PAGE_ID")
    token = _env("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise PublishActionError(
            "Set FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN before publishing."
        )

    version = _env("FACEBOOK_GRAPH_VERSION")
    graph = f"https://graph.facebook.com/{version}" if version else "https://graph.facebook.com"
    base = f"{graph}/{page_id}/video_reels"

    timeout = httpx.Timeout(120.0, connect=30.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        start = client.post(
            base,
            params={"access_token": token, "upload_phase": "start"},
        )
        start.raise_for_status()
        payload = start.json()
        video_id = str(payload.get("video_id") or "")
        upload_url = payload.get("upload_url")
        if not video_id or not upload_url:
            raise PublishActionError("Facebook did not return a Reel upload session.")

        size = path.stat().st_size
        with path.open("rb") as fh:
            uploaded = client.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Type": "application/octet-stream",
                },
                content=fh,
            )
        uploaded.raise_for_status()

        finish = client.post(
            base,
            params={
                "access_token": token,
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "description": description,
                "title": title,
            },
        )
        finish.raise_for_status()
        result = finish.json()
        if result.get("success") is not True:
            raise PublishActionError(f"Facebook Reel publish failed: {result}")

        external_url = None
        try:
            info = client.get(
                f"{graph}/{video_id}",
                params={"access_token": token, "fields": "permalink_url"},
            )
            if info.is_success:
                external_url = (info.json() or {}).get("permalink_url")
        except Exception:
            external_url = None

    return {"external_id": video_id, "external_url": external_url}


async def _job_progress(job_id: str, pct: float, step: str) -> None:
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager

    await update_job_status(job_id, "running", progress_pct=pct, current_step=step)
    await ws_manager.send_progress(job_id, pct, step, "local")


async def _execute_publish(
    *,
    job_id: str,
    publication_id: int,
    target_platform: str,
    publish_store: PublishStore,
    path: Path,
    title: str,
    caption: str,
    privacy: str | None,
    made_for_kids: bool,
) -> None:
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager

    try:
        if target_platform == "youtube":
            await _job_progress(job_id, 5, "Uploading to YouTube...")

            loop = asyncio.get_running_loop()

            def progress_cb(pct: float) -> None:
                asyncio.run_coroutine_threadsafe(
                    _job_progress(job_id, min(95.0, max(5.0, pct)), "Uploading to YouTube..."),
                    loop,
                )

            result = await asyncio.to_thread(
                _upload_youtube_sync,
                publish_store,
                path,
                title=title,
                description=caption,
                privacy=privacy or "private",
                made_for_kids=made_for_kids,
                progress_cb=progress_cb,
            )
        elif target_platform == "facebook":
            await _job_progress(job_id, 15, "Uploading Reel to Facebook...")
            result = await asyncio.to_thread(
                _publish_facebook_sync,
                path,
                title=title,
                description=caption,
            )
        else:
            raise PublishActionError(f"Unsupported auto-publish platform: {target_platform}")

        publish_store.finish_publication(
            publication_id,
            status="PUBLISHED",
            external_id=result.get("external_id"),
            external_url=result.get("external_url"),
        )
        await update_job_status(
            job_id,
            "success",
            progress_pct=100,
            current_step="Published",
            output_data={
                "platform": target_platform,
                "external_id": result.get("external_id"),
                "external_url": result.get("external_url"),
                "publication_id": publication_id,
            },
        )
        await ws_manager.send(
            {
                "type": "job_complete",
                "job_id": job_id,
                "result": result,
            },
            "local",
        )
    except Exception as exc:
        publish_store.finish_publication(
            publication_id,
            status="FAILED",
            error_message=str(exc),
        )
        await update_job_status(
            job_id,
            "failed",
            current_step="Publish failed",
            error_message=str(exc),
        )
        await ws_manager.send(
            {"type": "job_failed", "job_id": job_id, "error": str(exc)},
            "local",
        )


async def launch_publish(
    review_store: ReviewStore,
    publish_store: PublishStore,
    source_platform: str,
    content_id: str,
    target_platform: str,
    *,
    title: str,
    caption: str,
    privacy: str | None = None,
    made_for_kids: bool = False,
) -> dict:
    if target_platform not in {"youtube", "facebook"}:
        raise PublishActionError("Only Facebook and YouTube support API publishing.")

    detail = _approved_detail(review_store, source_platform, content_id)
    path = Path(detail["_final_path"])

    title = (title or "").strip()
    caption = (caption or "").strip()
    if not title:
        raise PublishActionError("Title is required.")
    if target_platform == "youtube" and privacy not in YOUTUBE_PRIVACY:
        raise PublishActionError("YouTube privacy must be private, unlisted, or public.")
    if target_platform == "facebook":
        ok, reason, _ = facebook_reel_eligibility(path)
        if not ok:
            raise PublishActionError(reason or "Final is not eligible for Facebook Reels.")
        if not facebook_configured():
            raise PublishActionError("Facebook Page publishing is not configured.")
    if target_platform == "youtube":
        if not youtube_oauth_configured():
            raise PublishActionError("YouTube OAuth client is not configured.")
        if not publish_store.get_account("youtube"):
            raise PublishActionError("YouTube is not connected.")

    latest = publish_store.latest_publication(source_platform, content_id, target_platform)
    if latest and latest.get("status") == "PUBLISHING":
        raise PublishActionError(f"{target_platform.title()} publish is already running.")

    from backend.agents.job_helper import create_job
    from backend.core.task_runner import dispatch

    job = await create_job(
        f"publish:{target_platform}",
        "local",
        {
            "source_platform": source_platform,
            "content_id": content_id,
            "target_platform": target_platform,
            "privacy": privacy,
        },
    )
    publication_id = publish_store.create_publication(
        source_platform,
        content_id,
        target_platform,
        job_id=job.id,
        title=title,
        caption=caption,
        privacy=privacy,
        status="PUBLISHING",
    )
    dispatch(
        _execute_publish(
            job_id=job.id,
            publication_id=publication_id,
            target_platform=target_platform,
            publish_store=publish_store,
            path=path,
            title=title,
            caption=caption,
            privacy=privacy,
            made_for_kids=made_for_kids,
        )
    )
    return {
        "job_id": job.id,
        "publication_id": publication_id,
        "status": "PUBLISHING",
        "platform": target_platform,
    }


def mark_tiktok_posted(
    review_store: ReviewStore,
    publish_store: PublishStore,
    source_platform: str,
    content_id: str,
    *,
    title: str,
    caption: str,
    external_url: str,
) -> dict:
    _approved_detail(review_store, source_platform, content_id)
    parsed = urlparse((external_url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        raise PublishActionError("Paste a valid https://...tiktok.com post URL.")

    pub_id = publish_store.create_publication(
        source_platform,
        content_id,
        "tiktok",
        title=(title or "").strip() or None,
        caption=(caption or "").strip() or None,
        status="PUBLISHED",
    )
    publish_store.finish_publication(
        pub_id,
        status="PUBLISHED",
        external_url=external_url.strip(),
    )
    return publish_store.latest_publication(source_platform, content_id, "tiktok") or {}


def publish_gate_status(
    review_store: ReviewStore,
    publish_store: PublishStore,
    source_platform: str,
    content_id: str,
) -> dict:
    detail = _approved_detail(review_store, source_platform, content_id)
    path = Path(detail["_final_path"])
    metadata = default_publish_metadata(detail)

    fb_ok = False
    fb_reason = None
    media = {}
    try:
        fb_ok, fb_reason, media = facebook_reel_eligibility(path)
    except Exception as exc:
        fb_reason = str(exc)

    return {
        "ready": True,
        "metadata": metadata,
        "media": media,
        "platforms": {
            "tiktok": {
                "mode": "manual",
                "available": True,
                "reason": "Manual post only; use Download + Copy caption.",
            },
            "facebook": {
                "mode": "api",
                "configured": facebook_configured(),
                "available": facebook_configured() and fb_ok,
                "reason": (
                    fb_reason
                    if not fb_ok
                    else (
                        None
                        if facebook_configured()
                        else "Set FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN."
                    )
                ),
            },
            "youtube": {
                "mode": "api",
                "oauth_configured": youtube_oauth_configured(),
                "connected": bool(publish_store.get_account("youtube")),
                "available": bool(
                    youtube_oauth_configured() and publish_store.get_account("youtube")
                ),
                "reason": (
                    None
                    if youtube_oauth_configured() and publish_store.get_account("youtube")
                    else (
                        "Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET."
                        if not youtube_oauth_configured()
                        else "Connect YouTube."
                    )
                ),
            },
        },
        "publications": publish_store.list_publications(source_platform, content_id),
    }
