"""Performance feedback API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..performance_service import (
    PerformanceActionError,
    add_manual_metrics,
    build_feedback_summary,
    publication_performance,
    sync_youtube_metrics,
)
from ..performance_store import PerformanceStore, PerformanceValidationError
from ..publish_store import PublishStore
from ..review_store import ReviewStore

router = APIRouter(prefix="/performance", tags=["content-performance"])


class ManualMetricRequest(BaseModel):
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    note: str | None = None


def _performance_store() -> PerformanceStore:
    return PerformanceStore()


def _review_store() -> ReviewStore:
    return ReviewStore()


def _publish_store() -> PublishStore:
    return PublishStore()


@router.get("/summary")
def performance_summary():
    try:
        return build_feedback_summary(_performance_store(), _review_store())
    except (PerformanceValidationError, PerformanceActionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/publications/{publication_id}")
def get_publication_performance(publication_id: int):
    try:
        return publication_performance(_performance_store(), publication_id)
    except (PerformanceValidationError, PerformanceActionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/publications/{publication_id}/metrics")
def add_publication_metrics(publication_id: int, request: ManualMetricRequest):
    try:
        snapshot = add_manual_metrics(
            _performance_store(),
            publication_id,
            views=request.views,
            likes=request.likes,
            comments=request.comments,
            shares=request.shares,
            note=request.note,
        )
        return {
            "snapshot": snapshot,
            "performance": publication_performance(_performance_store(), publication_id),
        }
    except (PerformanceValidationError, PerformanceActionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/publications/{publication_id}/sync-youtube")
def sync_publication_youtube(publication_id: int):
    try:
        snapshot = sync_youtube_metrics(
            _performance_store(),
            _publish_store(),
            publication_id,
        )
        return {
            "snapshot": snapshot,
            "performance": publication_performance(_performance_store(), publication_id),
        }
    except (PerformanceValidationError, PerformanceActionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
