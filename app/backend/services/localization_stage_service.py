# SPDX-License-Identifier: AGPL-3.0-only
"""Durable Phase-C localization stage/artifact manifest.

Adapted from YouDub's persisted stage runner/cache semantics and OpenCreator's
shared task/workspace state. This does NOT create a second job system: the
existing Job row remains the owner; this module persists expensive media
artifacts so a failed/restarted job can resume or rerun from a selected stage.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings

STAGES = (
    "separation",
    "asr",
    "semantic",
    "translation",
    "dubbing",
    "audio_mix",
    "final_render",
    "qc",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageRecord:
    status: str = "pending"  # pending|running|complete|failed|invalidated
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class LocalizationRun:
    def __init__(
        self,
        job_id: str,
        *,
        source_path: str | None = None,
        effective_profile: dict | None = None,
        request: dict | None = None,
    ):
        self.job_id = job_id
        self.root = settings.STORAGE_ROOT / "localization_jobs" / job_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        if self.manifest_path.exists():
            self.data = self._read()
        else:
            if not source_path:
                raise ValueError("source_path is required for a new localization run")
            self.data = {
                "version": 1,
                "job_id": job_id,
                "source_path": str(Path(source_path).resolve()),
                "effective_profile": effective_profile or {},
                "request": request or {},
                "created_at": _utc(),
                "updated_at": _utc(),
                "stages": {name: asdict(StageRecord()) for name in STAGES},
            }
            self._write()

    @classmethod
    def load(cls, job_id: str) -> "LocalizationRun":
        return cls(job_id)

    def _read(self) -> dict:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Localization manifest is unreadable: {self.manifest_path}") from exc
        for name in STAGES:
            data.setdefault("stages", {}).setdefault(name, asdict(StageRecord()))
        return data

    def _write(self) -> None:
        self.data["updated_at"] = _utc()
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, default=str) + "
",
            encoding="utf-8",
        )
        tmp.replace(self.manifest_path)

    @property
    def source_path(self) -> Path:
        return Path(self.data["source_path"])

    @property
    def effective_profile(self) -> dict:
        return dict(self.data.get("effective_profile") or {})

    @property
    def request(self) -> dict:
        return dict(self.data.get("request") or {})

    def stage_dir(self, stage: str) -> Path:
        self._check_stage(stage)
        path = self.root / stage
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact_path(self, stage: str, name: str) -> Path:
        return self.stage_dir(stage) / name

    def _check_stage(self, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown localization stage: {stage}")

    def record(self, stage: str) -> dict:
        self._check_stage(stage)
        return self.data["stages"][stage]

    def artifact(self, stage: str, key: str) -> Path | None:
        value = self.record(stage).get("artifacts", {}).get(key)
        if not value:
            return None
        path = Path(value)
        return path if path.exists() and path.stat().st_size > 0 else None

    def is_complete(self, stage: str, required_artifacts: tuple[str, ...] = ()) -> bool:
        record = self.record(stage)
        if record.get("status") != "complete":
            return False
        for key in required_artifacts:
            if self.artifact(stage, key) is None:
                return False
        return True

    def start(self, stage: str) -> None:
        record = self.record(stage)
        record.update(
            status="running",
            started_at=_utc(),
            completed_at=None,
            error=None,
        )
        self._write()

    def complete(
        self,
        stage: str,
        *,
        artifacts: dict[str, Path | str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        record = self.record(stage)
        serial_artifacts: dict[str, str] = {}
        for key, value in (artifacts or {}).items():
            path = Path(value)
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError(f"Cannot complete {stage}: missing artifact {key}: {path}")
            serial_artifacts[key] = str(path.resolve())
        record.update(
            status="complete",
            completed_at=_utc(),
            error=None,
            artifacts=serial_artifacts,
            metadata=metadata or {},
        )
        self._write()

    def fail(self, stage: str, error: Exception | str) -> None:
        record = self.record(stage)
        record.update(
            status="failed",
            completed_at=_utc(),
            error=str(error)[:2000],
        )
        self._write()

    def invalidate_from(self, stage: str, *, delete_artifacts: bool = True) -> list[str]:
        self._check_stage(stage)
        index = STAGES.index(stage)
        invalidated = list(STAGES[index:])
        for name in invalidated:
            record = self.record(name)
            if delete_artifacts:
                directory = self.root / name
                if directory.exists():
                    shutil.rmtree(directory, ignore_errors=True)
            record.update(
                status="invalidated",
                started_at=None,
                completed_at=None,
                error=None,
                artifacts={},
                metadata={},
            )
        self._write()
        return invalidated

    def first_incomplete_stage(self) -> str | None:
        for name in STAGES:
            if self.record(name).get("status") != "complete":
                return name
        return None

    def save_json(self, stage: str, name: str, payload: Any) -> Path:
        path = self.artifact_path(stage, name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "
",
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    def load_json(self, stage: str, key: str) -> Any:
        path = self.artifact(stage, key)
        if path is None:
            raise FileNotFoundError(f"Missing {stage}/{key} artifact")
        return json.loads(path.read_text(encoding="utf-8"))

    def update_effective_profile(self, profile: dict) -> None:
        self.data["effective_profile"] = dict(profile)
        self._write()

    def update_request(self, request: dict) -> None:
        self.data["request"] = dict(request)
        self._write()

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self.data))
