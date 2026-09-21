"""Persistence + file-backed data access for the Content Engine review console.

This module deliberately does not depend on ViralMint. The backend overlay imports it,
and root-level tests can exercise it without booting the media application.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_STAGE = "candidate"
REMAKE_STAGE = "remake"

CANDIDATE_STATUSES = {"REVIEW_PENDING", "APPROVED_FOR_REMAKE", "REJECTED"}
REMAKE_STATUSES = {"REMAKE_REVIEW", "APPROVED", "REWORK", "REJECTED"}

_SAFE_PLATFORM = {"douyin", "bilibili"}
_SAFE_CONTENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class ReviewValidationError(ValueError):
    pass


def find_project_root() -> Path:
    """Resolve the root repo even after this package is copied into tools/ViralMint."""
    explicit = os.getenv("CONTENT_ENGINE_ROOT")
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "radar.py").exists():
            return root
        raise RuntimeError(f"CONTENT_ENGINE_ROOT is not a Content Engine root: {root}")

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "radar.py").exists() and (parent / "data").is_dir():
            return parent
    raise RuntimeError(
        "Could not locate Content Engine root. Set CONTENT_ENGINE_ROOT to the repo path."
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class ReviewStore:
    def __init__(self, db_path: Path | None = None, project_root: Path | None = None):
        self.root = (project_root or find_project_root()).resolve()
        self.db_path = (db_path or (self.root / "data" / "radar.db")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS content_reviews (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform    TEXT NOT NULL,
                    content_id  TEXT NOT NULL,
                    stage       TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    note        TEXT,
                    reviewed_at TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    UNIQUE(platform, content_id, stage)
                );
                CREATE INDEX IF NOT EXISTS idx_content_reviews_status
                    ON content_reviews(status);
                CREATE INDEX IF NOT EXISTS idx_content_reviews_content
                    ON content_reviews(platform, content_id);
                """
            )

    @property
    def intelligence_path(self) -> Path:
        return self.root / "data" / "candidate_intelligence.json"

    def load_candidates(self) -> list[dict]:
        payload = _read_json(self.intelligence_path, {}) or {}
        candidates = payload.get("candidates") or []
        normalized: list[dict] = []
        for i, raw in enumerate(candidates, 1):
            c = dict(raw)
            c["rank"] = c.get("rank") or i
            normalized.append(c)
        return normalized

    def _candidate_dir(self, platform: str, content_id: str) -> Path:
        self._validate_identity(platform, content_id)
        return self.root / "data" / "candidates" / f"{platform}_{content_id}"

    def _remake_dir(self, platform: str, content_id: str) -> Path:
        self._validate_identity(platform, content_id)
        return self.root / "data" / "remakes" / f"{platform}_{content_id}"

    @staticmethod
    def _validate_identity(platform: str, content_id: str) -> None:
        if platform not in _SAFE_PLATFORM:
            raise ReviewValidationError(f"Unsupported platform: {platform}")
        if not _SAFE_CONTENT_ID.fullmatch(content_id or ""):
            raise ReviewValidationError("Invalid content_id")

    def remake_exists(self, platform: str, content_id: str) -> bool:
        return (self._remake_dir(platform, content_id) / "final.mp4").is_file()

    def source_exists(self, platform: str, content_id: str) -> bool:
        return (self._candidate_dir(platform, content_id) / "source.mp4").is_file()

    def resolve_media_path(self, kind: str, platform: str, content_id: str) -> Path:
        """Return a safe local media path; resolved paths may never escape their base."""
        self._validate_identity(platform, content_id)
        if kind == "source":
            base = (self.root / "data" / "candidates").resolve()
            path = (self._candidate_dir(platform, content_id) / "source.mp4").resolve()
        elif kind == "final":
            base = (self.root / "data" / "remakes").resolve()
            path = (self._remake_dir(platform, content_id) / "final.mp4").resolve()
        else:
            raise ReviewValidationError("kind must be source or final")

        if not path.is_relative_to(base):
            raise ReviewValidationError("Resolved media path escaped allowed root")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def _insert_initial_state(self, platform: str, content_id: str, stage: str, status: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO content_reviews
                    (platform, content_id, stage, status, note, reviewed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (platform, content_id, stage, status, now, now),
            )

    def seed_current_candidates(self, candidates: list[dict] | None = None) -> None:
        """Seed only missing states. Existing human decisions are never overwritten."""
        candidates = candidates if candidates is not None else self.load_candidates()
        for candidate in candidates:
            platform = str(candidate.get("platform") or "")
            content_id = str(candidate.get("content_id") or "")
            self._validate_identity(platform, content_id)
            if self.remake_exists(platform, content_id):
                self._insert_initial_state(platform, content_id, REMAKE_STAGE, "REMAKE_REVIEW")
            else:
                self._insert_initial_state(platform, content_id, CANDIDATE_STAGE, "REVIEW_PENDING")

    def _review_rows(self) -> dict[tuple[str, str, str], dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT platform, content_id, stage, status, note, reviewed_at, created_at, updated_at "
                "FROM content_reviews"
            ).fetchall()
        return {
            (r["platform"], r["content_id"], r["stage"]): dict(r)
            for r in rows
        }

    def get_review(self, platform: str, content_id: str, stage: str) -> dict | None:
        self._validate_identity(platform, content_id)
        if stage not in {CANDIDATE_STAGE, REMAKE_STAGE}:
            raise ReviewValidationError("Invalid review stage")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT platform, content_id, stage, status, note, reviewed_at, created_at, updated_at
                FROM content_reviews
                WHERE platform=? AND content_id=? AND stage=?
                """,
                (platform, content_id, stage),
            ).fetchone()
        return dict(row) if row else None

    def set_decision(
        self,
        platform: str,
        content_id: str,
        stage: str,
        status: str,
        note: str | None = None,
    ) -> dict:
        self._validate_identity(platform, content_id)
        note = (note or "").strip()

        if stage == CANDIDATE_STAGE:
            allowed = CANDIDATE_STATUSES
        elif stage == REMAKE_STAGE:
            allowed = REMAKE_STATUSES
        else:
            raise ReviewValidationError("Invalid review stage")

        if status not in allowed:
            raise ReviewValidationError(f"Status {status!r} is invalid for stage {stage!r}")
        if stage == REMAKE_STAGE and status == "REWORK" and not note:
            raise ReviewValidationError("REWORK requires a review note")
        if stage == REMAKE_STAGE and not self.remake_exists(platform, content_id):
            raise ReviewValidationError("Cannot review remake before final.mp4 exists")

        now = utc_now()
        reviewed_at = None if status in {"REVIEW_PENDING", "REMAKE_REVIEW"} else now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO content_reviews
                    (platform, content_id, stage, status, note, reviewed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, content_id, stage) DO UPDATE SET
                    status=excluded.status,
                    note=excluded.note,
                    reviewed_at=excluded.reviewed_at,
                    updated_at=excluded.updated_at
                """,
                (platform, content_id, stage, status, note or None, reviewed_at, now, now),
            )
        review = self.get_review(platform, content_id, stage)
        assert review is not None
        return review

    @staticmethod
    def _risk_summary(intel: dict) -> list[str]:
        risks = (intel or {}).get("risks") or {}
        labels = {
            "copyright_dependency": "copyright",
            "personality_dependency": "personality",
            "product_dependency": "product",
            "context_dependency": "context",
        }
        return [
            f"{labels.get(k, k)}:{v}"
            for k, v in risks.items()
            if v and str(v).upper() != "LOW"
        ]

    def list_candidates(self, status: str | None = None) -> dict:
        candidates = self.load_candidates()
        self.seed_current_candidates(candidates)
        reviews = self._review_rows()

        items: list[dict] = []
        for c in candidates:
            platform = str(c["platform"])
            content_id = str(c["content_id"])
            has_remake = self.remake_exists(platform, content_id)
            stage = REMAKE_STAGE if has_remake else CANDIDATE_STAGE
            review = reviews.get((platform, content_id, stage))
            current_status = review["status"] if review else (
                "REMAKE_REVIEW" if has_remake else "REVIEW_PENDING"
            )
            if status and status != "ALL" and current_status != status:
                continue

            intel = c.get("intelligence") or {}
            items.append(
                {
                    "rank": c.get("rank"),
                    "platform": platform,
                    "content_id": content_id,
                    "title": c.get("title"),
                    "creator": c.get("creator"),
                    "url": c.get("url"),
                    "radar_score": c.get("radar_score"),
                    "radar_confidence": c.get("radar_confidence"),
                    "radar_breakdown": c.get("radar_breakdown") or {},
                    "localization_potential": intel.get("localization_potential"),
                    "remake_difficulty": intel.get("remake_difficulty"),
                    "risk_summary": self._risk_summary(intel),
                    "source_exists": self.source_exists(platform, content_id),
                    "remake_exists": has_remake,
                    "review_stage": stage,
                    "review_status": current_status,
                    "review_note": review.get("note") if review else None,
                }
            )

        # Summary is always over all candidates, independent from the UI filter.
        all_reviews = reviews
        current_rows = []
        for c in candidates:
            platform = str(c["platform"])
            content_id = str(c["content_id"])
            has_remake = self.remake_exists(platform, content_id)
            stage = REMAKE_STAGE if has_remake else CANDIDATE_STAGE
            r = all_reviews.get((platform, content_id, stage))
            current_rows.append((has_remake, r["status"] if r else None))

        def count_status(s: str) -> int:
            return sum(1 for _, current in current_rows if current == s)

        summary = {
            "candidates_analyzed": len(candidates),
            "pending_review": count_status("REVIEW_PENDING"),
            "approved_for_remake": count_status("APPROVED_FOR_REMAKE"),
            "rendered": sum(1 for has_remake, _ in current_rows if has_remake),
            "awaiting_remake_review": count_status("REMAKE_REVIEW"),
            "approved": count_status("APPROVED"),
            "rework": count_status("REWORK"),
            "rejected": count_status("REJECTED"),
        }
        return {"summary": summary, "candidates": items}

    def get_candidate_detail(self, platform: str, content_id: str) -> dict:
        self._validate_identity(platform, content_id)
        candidates = self.load_candidates()
        candidate = next(
            (
                c for c in candidates
                if str(c.get("platform")) == platform and str(c.get("content_id")) == content_id
            ),
            None,
        )
        if candidate is None:
            raise KeyError((platform, content_id))

        self.seed_current_candidates([candidate])
        cdir = self._candidate_dir(platform, content_id)
        rdir = self._remake_dir(platform, content_id)
        has_remake = self.remake_exists(platform, content_id)
        current_stage = REMAKE_STAGE if has_remake else CANDIDATE_STAGE

        detail = dict(candidate)
        detail.update(
            {
                "source_exists": self.source_exists(platform, content_id),
                "remake_exists": has_remake,
                "current_stage": current_stage,
                "candidate_review": self.get_review(platform, content_id, CANDIDATE_STAGE),
                "remake_review": self.get_review(platform, content_id, REMAKE_STAGE),
                "metadata": _read_json(cdir / "metadata.json", {}),
                "transcript": _read_json(cdir / "transcript.json", {}),
                "adaptation": _read_json(rdir / "adaptation.json", {}),
                "segment_plan": _read_json(rdir / "segment_plan.json", {}),
            }
        )
        return detail
