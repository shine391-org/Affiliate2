# SPDX-License-Identifier: AGPL-3.0-only
"""Durable Phase-C localization stage/artifact manifest.

Adapted from YouDub's persisted stage runner/cache semantics and OpenCreator's
shared task/workspace state. This does NOT create a second job system: the
existing Job row remains the owner; this module persists expensive media
artifacts so a failed/restarted job can resume or rerun from a selected stage.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings

MANIFEST_VERSION = 2

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


def _source_identity(path: Path) -> dict[str, Any]:
    """Return the content identity used to decide whether resume is safe.

    The path is deliberately not part of the identity: replacing bytes at the
    same filename is exactly the corruption case this guard must detect.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
    }


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
                "version": MANIFEST_VERSION,
                "job_id": job_id,
                "source_path": str(Path(source_path).resolve()),
                "source_identity": None,
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
        version = int(data.get("version") or 0)
        if version != MANIFEST_VERSION:
            raise RuntimeError(
                f"Localization manifest version {version} is incompatible with "
                f"current pipeline version {MANIFEST_VERSION}; start a fresh "
                "localization job so stale artifacts are not resumed."
            )
        for name in STAGES:
            data.setdefault("stages", {}).setdefault(name, asdict(StageRecord()))
        return data

    def _write(self) -> None:
        self.data["updated_at"] = _utc()
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.manifest_path)

    def _has_completed_stages(self) -> bool:
        return any(
            self.record(name).get("status") == "complete"
            for name in STAGES
        )

    def ensure_source_copy(self, source: Path | str) -> Path:
        """Persist one immutable-by-identity source snapshot for this run."""
        source = Path(source).resolve()
        current = Path(self.data["source_path"]).resolve()
        try:
            if current.exists() and current.is_relative_to(self.root.resolve()):
                self.verify_source_identity()
                return current
        except (OSError, ValueError):
            pass

        suffix = source.suffix.lower() or ".mp4"
        target = self.root / f"source{suffix}"
        if source != target.resolve():
            tmp = target.with_suffix(target.suffix + ".tmp")
            shutil.copy2(source, tmp)
            tmp.replace(target)
        if not target.exists() or target.stat().st_size <= 0:
            raise RuntimeError(f"Persisted source media is missing or empty: {target}")

        self.data["source_path"] = str(target.resolve())
        self.data["source_identity"] = _source_identity(target)
        self._write()
        return target

    def verify_source_identity(self) -> dict[str, Any]:
        """Fail closed when a resumable run's source bytes have changed."""
        path = self.source_path.resolve()
        if not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError(f"Persisted source media is missing or empty: {path}")

        expected = self.data.get("source_identity")
        if not expected:
            if self._has_completed_stages():
                raise RuntimeError(
                    "Localization source identity is missing for a run with completed "
                    "stages. Existing artifacts cannot be safely resumed; rerun from "
                    "separation using the current source media."
                )
            current = _source_identity(path)
            self.data["source_identity"] = current
            self._write()
            return current
        if not isinstance(expected, dict):
            raise RuntimeError(
                "Localization source identity is invalid. Existing artifacts cannot "
                "be safely resumed; rerun from separation using the current source media."
            )

        current = _source_identity(path)
        try:
            expected_size = int(expected.get("size_bytes"))
        except (TypeError, ValueError):
            expected_size = -1
        if (
            str(expected.get("sha256") or "") != current["sha256"]
            or expected_size != current["size_bytes"]
        ):
            raise RuntimeError(
                "Persisted source media has changed since this localization run was "
                "created. Existing separation/ASR/translation artifacts cannot be "
                "safely resumed; rerun from separation using the current source media."
            )
        return current

    def refresh_source_identity(self) -> dict[str, Any]:
        """Accept the current source only after every stage was invalidated."""
        if self._has_completed_stages():
            raise RuntimeError(
                "Cannot accept changed source media while completed localization "
                "stages remain; invalidate from separation first."
            )
        path = self.source_path.resolve()
        if not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError(f"Persisted source media is missing or empty: {path}")
        current = _source_identity(path)
        self.data["source_identity"] = current
        self._write()
        return current

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

    def mark_complete_without_artifacts(
        self,
        stage: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        self._check_stage(stage)
        record = self.record(stage)
        record.update(
            status="complete",
            completed_at=_utc(),
            error=None,
            artifacts={},
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
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
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
