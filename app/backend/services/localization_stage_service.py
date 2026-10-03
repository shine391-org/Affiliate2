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


def compact_localization_workspace(job_id: str) -> int:
    """Drop reconstructible heavy intermediates while preserving fast rerender.

    Keep the immutable source, manifest, translation/QC/final artifacts and the
    *declared* dubbing/audio-mix artifacts required by _assert_rerender_ready.
    Raw TTS chunks, fitted variants, speaker references, Demucs stems and
    ASR/semantic scratch are reconstructible and may be discarded.
    """
    parent = (settings.STORAGE_ROOT / "localization_jobs").resolve()
    root = (parent / str(job_id)).resolve()
    if not root.is_relative_to(parent) or root == parent:
        raise ValueError("Unsafe localization workspace path")
    manifest_path = root / "manifest.json"
    if not root.exists() or not manifest_path.is_file():
        return 0

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Localization manifest is unreadable: {manifest_path}"
        ) from exc

    removed = 0

    # These stages are never required for render-only replay once translation,
    # dub_track and mixed_audio are durable.
    for name in ("separation", "asr", "semantic"):
        directory = root / name
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
            if not directory.exists():
                removed += 1

    # Dubbing can contain hundreds of raw/fitted TTS files. Keep only the
    # canonical artifacts named by the manifest (transcript, dub_track, plan,
    # chunks); delete every other file/directory.
    for stage in ("dubbing", "audio_mix"):
        directory = root / stage
        if not directory.exists():
            continue

        record = (data.get("stages") or {}).get(stage) or {}
        keep: set[Path] = set()
        for value in (record.get("artifacts") or {}).values():
            try:
                path = Path(value).resolve()
            except (TypeError, OSError, ValueError):
                continue
            if path.is_relative_to(directory.resolve()):
                keep.add(path)

        for item in list(directory.iterdir()):
            try:
                resolved = item.resolve()
            except OSError:
                continue
            if resolved in keep:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
                if not item.exists():
                    removed += 1
            else:
                try:
                    item.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass

    return removed


def cleanup_localization_workspace(job_id: str) -> bool:
    """Delete one durable localization workspace after the owning job is deleted.

    Cancellation alone intentionally keeps it resumable. This is only for an
    explicit DELETE of the job/Library asset; otherwise Demucs stems, TTS chunks,
    source snapshots and render candidates would survive forever after the row
    that can reach them is gone.
    """
    parent = (settings.STORAGE_ROOT / "localization_jobs").resolve()
    root = (parent / str(job_id)).resolve()
    if not root.is_relative_to(parent) or root == parent:
        raise ValueError("Unsafe localization workspace path")
    if not root.exists():
        return False
    shutil.rmtree(root)
    return True


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

    def invalidate_from(
        self,
        stage: str,
        *,
        delete_artifacts: bool = True,
        preserve_files: set[str] | list[str] | None = None,
    ) -> list[str]:
        self._check_stage(stage)
        index = STAGES.index(stage)
        invalidated = list(STAGES[index:])
        preserved_names = set(preserve_files or ())

        for name in invalidated:
            record = self.record(name)
            durable_audit = self.root / "dubbing" / "rewrite_audit.json"
            if delete_artifacts:
                directory = self.root / name
                if directory.exists():
                    if name == "dubbing" or (name == "translation" and "checkpoint.json" in preserved_names):
                        for item in list(directory.iterdir()):
                            if item.name in preserved_names or (name == "dubbing" and item.name == "rewrite_audit.json"):
                                continue
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                try:
                                    item.unlink(missing_ok=True)
                                except Exception:
                                    pass
                        if not any(directory.iterdir()):
                            directory.rmdir()
                    else:
                        shutil.rmtree(directory, ignore_errors=True)
            record.update(
                status="invalidated",
                started_at=None,
                completed_at=None,
                error=None,
                artifacts={},
                metadata={},
            )
            if name == "dubbing" and durable_audit.exists():
                record["artifacts"]["rewrite_audit"] = str(durable_audit.resolve())
        if index <= STAGES.index("asr"):
            self.invalidate_dubbing_state()
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
        keys = ("tts_provider", "tts_connection_id", "tts_model", "tts_voice", "tts_base_url", "tts_region")
        if any(self.effective_profile.get(key) != profile.get(key) for key in keys):
            if (self.root / "dubbing_state.json").exists():
                self.invalidate_dubbing_state()
                profile = {**profile, "speaker_voices": {}, "speaker_reference_audio": {}}
        self.data["effective_profile"] = dict(profile)
        self._write()

    def invalidate_dubbing_state(self) -> None:
        path = self.root / "dubbing_state.json"
        if path.exists():
            from backend.services.dubbing_job_state import write_json_atomic
            state = json.loads(path.read_text(encoding="utf-8"))
            state.update(binding=None, speaker_voices={}, measurements={}, available_voices=[],
                         detected_speakers=[], shared_voice=False)
            write_json_atomic(path, state)

    def update_request(self, request: dict) -> None:
        self.data["request"] = dict(request)
        self._write()

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self.data))


class ManifestTransaction:
    """Best-effort atomicity around one multi-step manifest mutation.

    ``invalidate_from`` deletes artifact trees and rewrites the manifest in a
    single pass. A caller that must ALSO adopt a new effective profile/request
    and then hand control to a background runner needs both halves to survive
    or neither: a failure after invalidation would otherwise leave a job whose
    manifest says its artifacts are gone, whose bytes are already deleted, and
    whose rerun was never dispatched.

    ``begin()`` snapshots the manifest bytes plus the artifact trees of the
    stages that may change; ``rollback()`` restores them; ``commit()`` drops
    the snapshot. This is deliberately NOT a second invalidation engine and
    not a general transaction system: it wraps exactly the files
    ``LocalizationRun`` owns for a bounded stage window, and it is the only
    rollback mechanism the localization control plane uses.
    """

    _DIRNAME = ".rollback"

    def __init__(self, run: "LocalizationRun", stage_names) -> None:
        self.run = run
        self.stage_names = [name for name in stage_names if name in STAGES]
        self.backup_root = run.root / self._DIRNAME
        self._active = False

    def begin(self) -> None:
        if self._active:
            return
        if self.backup_root.exists():
            raise RuntimeError("Unresolved rebind backup exists; restore it before rebinding again.")
        stages_root = self.backup_root / "stages"
        try:
            stages_root.mkdir(parents=True, exist_ok=True)
            manifest_bytes = self.run.manifest_path.read_bytes()
            (self.backup_root / "manifest.json").write_bytes(manifest_bytes)
            state = self.run.root / "dubbing_state.json"
            if state.exists():
                shutil.copy2(state, self.backup_root / state.name)
            for name in self.stage_names:
                source = self.run.root / name
                if source.is_dir():
                    shutil.copytree(source, stages_root / name)
        except Exception:
            # Preparation has not touched the originals. A partial backup is
            # not recoverable state and must not prevent a safe retry.
            shutil.rmtree(self.backup_root, ignore_errors=True)
            raise
        self._active = True

    def rollback(self) -> None:
        if not self._active:
            return
        # Restore artifacts before publishing the old manifest. If restoration
        # fails, retain the backup so an IO failure cannot destroy the last copy.
        stages_root = self.backup_root / "stages"
        for name in self.stage_names:
            current = self.run.root / name
            backup = stages_root / name
            if current.exists():
                shutil.rmtree(current)
            if backup.is_dir():
                shutil.copytree(backup, current)
        manifest_bytes = (self.backup_root / "manifest.json").read_bytes()
        state = self.run.root / "dubbing_state.json"
        backup_state = self.backup_root / state.name
        if backup_state.exists():
            shutil.copy2(backup_state, state)
        else:
            state.unlink(missing_ok=True)
        tmp = self.run.manifest_path.with_suffix(".json.rollback")
        tmp.write_bytes(manifest_bytes)
        tmp.replace(self.run.manifest_path)
        self.run.data = json.loads(manifest_bytes)
        shutil.rmtree(self.backup_root, ignore_errors=True)
        self._active = False

    def commit(self) -> None:
        shutil.rmtree(self.backup_root, ignore_errors=True)
        self._active = False

    def __enter__(self) -> "ManifestTransaction":
        self.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False
