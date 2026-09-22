"""FastAPI routes for the Content Engine human review console."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..review_store import (
    CANDIDATE_STAGE,
    REMAKE_STAGE,
    ReviewStore,
    ReviewValidationError,
)

router = APIRouter(prefix="/review", tags=["content-review"])


class ReviewDecision(BaseModel):
    stage: Literal["candidate", "remake"]
    status: str
    note: str | None = None


def _store() -> ReviewStore:
    return ReviewStore()


@router.get("/candidates")
def list_candidates(status: str | None = Query(default=None)):
    try:
        return _store().list_candidates(status=status)
    except (ReviewValidationError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/candidates/{platform}/{content_id}")
def candidate_detail(platform: str, content_id: str):
    store = _store()
    try:
        return store.get_candidate_detail(platform, content_id)
    except ReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Candidate not found") from exc


@router.post("/candidates/{platform}/{content_id}/decision")
def save_decision(platform: str, content_id: str, decision: ReviewDecision):
    store = _store()
    try:
        # Ensure the candidate actually exists before accepting a review row.
        store.get_candidate_detail(platform, content_id)
        review = store.set_decision(
            platform=platform,
            content_id=content_id,
            stage=decision.stage,
            status=decision.status,
            note=decision.note,
        )
        return {"ok": True, "review": review}
    except ReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Candidate not found") from exc


@router.get("/media/{kind}/{platform}/{content_id}")
def review_media(
    kind: Literal["source", "final"],
    platform: str,
    content_id: str,
):
    store = _store()
    try:
        path = store.resolve_media_path(kind, platform, content_id)
    except ReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc

    # FileResponse handles range requests in modern Starlette, which keeps
    # seeking in the browser video player efficient for local files.
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=None,
        headers={"Cache-Control": "no-store"},
    )
