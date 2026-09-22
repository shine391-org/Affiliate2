"""Publish Gate API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..publish_service import (
    PublishActionError,
    launch_publish,
    mark_tiktok_posted,
    publish_gate_status,
    youtube_authorization_url,
    youtube_exchange_code,
)
from ..publish_store import PublishStore, PublishValidationError
from ..review_store import ReviewStore, ReviewValidationError

router = APIRouter(prefix="/publish", tags=["content-publish"])


class PublishRequest(BaseModel):
    platform: str
    title: str
    caption: str
    privacy: str | None = None
    made_for_kids: bool = False


class TikTokPostedRequest(BaseModel):
    title: str = ""
    caption: str = ""
    external_url: str


def _review_store() -> ReviewStore:
    return ReviewStore()


def _publish_store() -> PublishStore:
    return PublishStore()


@router.get("/candidates/{source_platform}/{content_id}")
def get_publish_gate(source_platform: str, content_id: str):
    try:
        return publish_gate_status(
            _review_store(),
            _publish_store(),
            source_platform,
            content_id,
        )
    except (PublishActionError, ReviewValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Candidate not found") from exc


@router.post("/candidates/{source_platform}/{content_id}")
async def publish_candidate(
    source_platform: str,
    content_id: str,
    request: PublishRequest,
):
    try:
        return await launch_publish(
            _review_store(),
            _publish_store(),
            source_platform,
            content_id,
            request.platform,
            title=request.title,
            caption=request.caption,
            privacy=request.privacy,
            made_for_kids=request.made_for_kids,
        )
    except (PublishActionError, PublishValidationError) as exc:
        status = 409 if "already" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except (ReviewValidationError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/{source_platform}/{content_id}/tiktok-posted")
def mark_manual_tiktok_posted(
    source_platform: str,
    content_id: str,
    request: TikTokPostedRequest,
):
    try:
        return mark_tiktok_posted(
            _review_store(),
            _publish_store(),
            source_platform,
            content_id,
            title=request.title,
            caption=request.caption,
            external_url=request.external_url,
        )
    except (PublishActionError, ReviewValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Candidate not found") from exc


@router.get("/youtube/connect")
def connect_youtube():
    try:
        return {"authorization_url": youtube_authorization_url(_publish_store())}
    except PublishActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/youtube/callback", response_class=HTMLResponse)
def youtube_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    try:
        youtube_exchange_code(_publish_store(), state, code)
    except Exception as exc:
        # Never echo OAuth provider details/tokens/codes into the browser.
        raise HTTPException(
            status_code=400,
            detail="YouTube connect failed. Check OAuth client and redirect configuration, then retry.",
        ) from exc

    return HTMLResponse(
        """
        <!doctype html>
        <html>
          <head><title>YouTube connected</title></head>
          <body style="font-family:system-ui;padding:32px">
            <h2>YouTube connected</h2>
            <p>You can close this tab and return to the Review Queue.</p>
            <script>
              setTimeout(() => window.close(), 1200)
            </script>
          </body>
        </html>
        """
    )


@router.delete("/youtube/disconnect")
def disconnect_youtube():
    store = _publish_store()
    store.delete_account("youtube")
    return {"ok": True}
