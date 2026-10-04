# SPDX-License-Identifier: AGPL-3.0-only
"""Durable state owned by one localization job, independent of stage caches."""
from __future__ import annotations

import hashlib
import json
import asyncio
import uuid
from pathlib import Path

from backend.services.dubbing_planner import DubbingRateCalibrator


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def tts_binding(runtime, target_language: str) -> dict:
    return {
        "tts_connection_id": runtime.connection_id,
        "tts_provider": getattr(runtime.provider, "value", str(runtime.provider)),
        "tts_model": runtime.model, "tts_voice": runtime.default_voice,
        "tts_base_url": runtime.base_url, "tts_region": runtime.region,
        "target_language": target_language.lower(),
    }


def reference_identity(path: str | None) -> dict | None:
    if not path:
        return None
    source = Path(path)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return {"path": str(source.resolve()), "missing": True}
    return {"path": str(source.resolve()), "sha256": digest.hexdigest()}


class DubbingJobState:
    def __init__(self, path: Path, *, scope: str, binding: dict, audit_path: Path | None = None):
        self.path = path
        if path.exists():
            # A corrupt audit is not a disposable cache: never silently reset it.
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict) or self.data.get("version") != 1:
                raise ValueError("Invalid dubbing job state")
        else:
            self.data = {"version": 1, "scope": scope, "rewrite_audit": []}
            if audit_path and audit_path.exists():
                # Upgrade old jobs without erasing their only before/after record.
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                entries = audit.get("entries") if isinstance(audit, dict) else None
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) or not {"segment_id", "before", "after"} <= entry.keys()
                    for entry in entries
                ):
                    raise ValueError("Invalid historical rewrite audit")
                self.data["rewrite_audit"] = entries
        if self.data.get("scope") != scope:
            self.data = {"version": 1, "scope": scope, "rewrite_audit": []}
        if self.data.get("binding") != binding:
            self.data.update(binding=binding, speaker_voices={}, measurements={}, available_voices=[], voice_catalogue=[], accepted_text={},
                             acceptance_key=None, acceptance_input_key=None, measured_allocation=None,
                             voice_warnings=[], voice_catalog_error=None, voice_catalogue_loaded=False)
        for entry in self.data["rewrite_audit"]:
            entry.setdefault("acceptance_key", "legacy-unscoped")
        self.save()

    def activate_revision(self, input_key: str) -> str:
        if self.data.get("acceptance_input_key") != input_key or not self.data.get("acceptance_key"):
            # c04d76e stored the input hash directly as acceptance_key. Preserve
            # its replay only when those exact inputs still match, and migrate
            # only the latest accepted entry proving each replayed text.
            legacy_replay = (not self.data.get("acceptance_input_key")
                             and self.data.get("acceptance_key") == input_key)
            accepted = self.data.get("accepted_text", {}) if legacy_replay else {}
            revision = uuid.uuid4().hex
            pending = dict(accepted)
            for entry in reversed(self.data["rewrite_audit"]):
                segment = entry["segment_id"]
                if (segment in pending and entry.get("status", "accepted") == "accepted"
                        and entry["after"] == pending[segment]):
                    entry["acceptance_key"] = revision
                    pending.pop(segment)
            self.data.update(acceptance_input_key=input_key, acceptance_key=revision, accepted_text=accepted)
        self.save()
        return self.data["acceptance_key"]

    def save(self) -> None:
        self.data["rewrite_count"] = self.rewrite_count
        write_json_atomic(self.path, self.data)

    @property
    def rewrite_count(self) -> int:
        return len({entry["segment_id"] for entry in self.data["rewrite_audit"]
                    if self.data.get("acceptance_key") and entry.get("acceptance_key") == self.data["acceptance_key"]
                    and entry.get("status", "accepted") == "accepted" and (
                        entry["before"] != entry["after"]
                        or entry.get("caption_before") != entry.get("caption_after"))})

    def observe(self, identity: str, voice_key: str, text: str,
                estimated_ms: int, actual_ms: int) -> None:
        if estimated_ms <= 0 or actual_ms <= 0:
            return
        self.data.setdefault("measurements", {})[identity] = {
            "voice_key": voice_key, "text": text,
            "estimated_ms": estimated_ms, "actual_ms": actual_ms,
        }
        self.save()

    def calibration(self) -> dict:
        groups: dict[str, DubbingRateCalibrator] = {}
        for measurement in self.data.get("measurements", {}).values():
            calibrator = groups.setdefault(measurement["voice_key"], DubbingRateCalibrator())
            calibrator.observe(measurement["estimated_ms"], measurement["actual_ms"])
        return {key: value.as_dict() for key, value in groups.items()}

    def audit_payload(self) -> dict:
        return {"rewrite_count": self.rewrite_count, "acceptance_key": self.data.get("acceptance_key"),
                "entries": self.data["rewrite_audit"],
                "rate_calibration": self.calibration()}


def speaker_voice_warning(code, speaker_id, source, voice, gender, provider, reason):
    return {"code": code, "speaker_id": speaker_id,
            "source_gender": source.get("gender", "unknown"),
            "selected_voice": voice, "selected_voice_gender": gender,
            "provider": provider, "reason": reason, "requires_human_review": True}


def speaker_gender_warnings(speaker_id, source, voice, gender, provider):
    warnings = []
    wanted = source.get("gender", "unknown")
    if wanted == "unknown" or not source.get("confirmed") or gender == "unknown":
        warnings.append(speaker_voice_warning("speaker_gender_unverified", speaker_id, source, voice, gender,
                                             provider, "source_or_selected_voice_gender_unverified"))
    if source.get("confirmed") and wanted in {"male", "female"} and gender in {"male", "female"} and gender != wanted:
        warnings.append(speaker_voice_warning("speaker_gender_mismatch", speaker_id, source, voice, gender,
                                             provider, "selected_voice_conflicts_with_confirmed_source"))
    return warnings


async def available_speaker_voices(runtime, language_voice: str) -> list[str]:
    return [row["voice_id"] for row in await speaker_voice_catalogue(runtime, language_voice)]


def voice_gender(row: dict) -> str:
    # Prefer provider gender metadata, not a name or position in a voice list.
    value = str(row.get("gender") or row.get("Gender") or (row.get("labels") or {}).get("gender")
                or row.get("category") or "").lower()
    return "female" if "female" in value else ("male" if "male" in value else "unknown")


async def speaker_voice_catalogue(runtime, language_voice: str) -> list[dict]:
    from backend.services import tts_service
    provider = getattr(runtime.provider, "value", str(runtime.provider))
    if provider == "gpt_sovits":
        return []  # Clone identity is the actual reference, not an ignored voice ID.
    options = {"region": runtime.region} if provider == "azure_tts" else {}
    rows = await asyncio.wait_for(tts_service.list_voices(
        runtime.provider, api_key=runtime.api_key, base_url=runtime.base_url, **options), timeout=10)
    if not rows:
        raise RuntimeError("Voice catalogue is empty or unavailable")
    catalogue = {}
    # These multilingual personas support Vietnamese. Only add them when the
    # selected provider actually advertises them; Azure availability alone is
    # not proof of Edge availability. Prefer native Vietnamese voices first.
    multilingual_vi = {"en-US-AndrewMultilingualNeural", "en-US-BrianMultilingualNeural",
                       "en-US-AvaMultilingualNeural", "en-US-EmmaMultilingualNeural"}
    for row in rows:
        voice = str(row.get("voice_id") or row.get("ShortName") or "")
        if not voice:
            continue
        if provider in {"edge_tts", "azure_tts"} and not (
            voice[:2] == language_voice[:2]
            or (language_voice.startswith("vi-") and voice in multilingual_vi)
        ):
            continue
        catalogue[voice] = {"voice_id": voice, "gender": voice_gender(row),
                            "locale": row.get("locale") or row.get("Locale") or ""}
    return list(catalogue.values())


async def resolve_speaker_voices(transcript, *, profile, runtime, state: DubbingJobState,
                                 language_voice: str) -> dict[str, str]:
    speakers = sorted({seg.speaker_id for seg in transcript.segments if seg.speaker_id})
    detected = set(speakers)
    for seg in transcript.segments:
        for word in seg.source_words:
            detected.update(word.get("speaker_ids") or [])
            if word.get("speaker_id"):
                detected.add(word["speaker_id"])
    saved = state.data.get("speaker_voices", {})
    mapping = {speaker: voice for speaker, voice in saved.items() if speaker in detected}
    mapping.update({speaker: voice for speaker, voice in profile.speaker_voices.items()
                    if speaker in detected and voice.strip()})
    shared = profile.speaker_voice_mode == "shared"
    provider = getattr(runtime.provider, "value", str(runtime.provider))
    source_genders = {speaker: dict(value) for speaker, value in
                      ((transcript.metadata.get("alignment") or {}).get("speaker_genders") or {}).items()
                      if speaker in detected}
    source_genders.update({speaker: {"gender": gender, "source": "reviewer", "confirmed": gender != "unknown"}
                           for speaker, gender in profile.speaker_genders.items() if speaker in detected})
    catalogue = list(state.data.get("voice_catalogue") or [])
    if not catalogue and provider in {"edge_tts", "azure_tts"} and language_voice.startswith("vi-"):
        catalogue = [{"voice_id": voice, "gender": gender, "locale": "vi-VN"}
                     for voice, gender in (("vi-VN-HoaiMyNeural", "female"), ("vi-VN-NamMinhNeural", "male"))]
    if detected and provider != "gpt_sovits" and not state.data.get("voice_catalogue_loaded"):
        try:
            catalogue = await speaker_voice_catalogue(runtime, language_voice)
            state.data.pop("voice_catalog_error", None)
            state.data["voice_catalogue_loaded"] = True
        except Exception as exc:
            state.data["voice_catalog_error"] = type(exc).__name__
    genders = {row["voice_id"]: row["gender"] for row in catalogue}
    # Confirmed built-in/provider metadata wins over a contradictory manual tag.
    genders = {**profile.voice_genders, **{key: value for key, value in genders.items() if value != "unknown"}}
    for speaker, voice in list(mapping.items()):
        wanted = source_genders.get(speaker, {}).get("gender", "unknown")
        if speaker not in profile.speaker_voices and wanted != "unknown" and genders.get(voice) != wanted:
            mapping.pop(speaker)
    missing = sorted((speaker for speaker in detected if speaker not in mapping),
                     key=lambda speaker: (source_genders.get(speaker, {}).get("gender", "unknown") == "unknown", speaker))
    warnings = []
    candidates = list(dict.fromkeys([runtime.default_voice, *[row["voice_id"] for row in catalogue]]))
    if shared:
        mapping = {speaker: runtime.default_voice for speaker in detected}
    elif provider != "gpt_sovits":
        used = set(mapping.values())
        for speaker in missing:
            wanted = source_genders.get(speaker, {}).get("gender", "unknown")
            compatible = [voice for voice in candidates if voice
                          and (wanted == "unknown" or genders.get(voice) == wanted)]
            voice = next((voice for voice in compatible if voice not in used), None)
            mapping[speaker] = voice or next(iter(compatible), runtime.default_voice)
            used.add(mapping[speaker])
    # Recompute warnings from the persisted choices so resume/chunk reuse does
    # not lose the reason a job needs review. Explicit shared mode is intentional.
    seen = set()
    for speaker in sorted(detected, key=lambda speaker: (speaker not in profile.speaker_voices, speaker)):
        voice = mapping.get(speaker, runtime.default_voice)
        source = source_genders.get(speaker, {})
        gender = genders.get(voice, "unknown") if provider != "gpt_sovits" else "unknown"
        automatic = not shared and speaker not in profile.speaker_voices and provider != "gpt_sovits"
        wanted = source.get("gender", "unknown")
        if automatic:
            if wanted != "unknown" and gender != wanted:
                warnings.append(speaker_voice_warning("speaker_voice_default_fallback", speaker, source, voice,
                    gender, provider, "no_compatible_voice_available"))
            elif voice in seen:
                warnings.append(speaker_voice_warning("speaker_voice_reused", speaker, source, voice,
                    gender, provider, "no_second_compatible_voice_selected"))
            if state.data.get("voice_catalog_error"):
                warnings.append(speaker_voice_warning("speaker_voice_catalog_unavailable", speaker, source, voice,
                    gender, provider, "catalogue_unavailable:" + state.data["voice_catalog_error"]))
        seen.add(voice)
        warnings.extend(speaker_gender_warnings(speaker, source, voice, gender, provider))
    state.data.update(speaker_voices=mapping, detected_speakers=sorted(detected),
                      available_voices=[row["voice_id"] for row in catalogue], voice_catalogue=catalogue,
                      speaker_genders=source_genders, voice_genders=genders, shared_voice=shared,
                      voice_warnings=warnings)
    state.save()
    return mapping
