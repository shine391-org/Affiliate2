# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""TTS voice preview endpoint using the exact unsaved Settings form values."""
import logging
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.config import settings
from backend.core.exceptions import VoiceGenerationError

logger = logging.getLogger(__name__)
router = APIRouter()

_PREVIEW_TEXT = "Xin chào, đây là bản thử giọng."
_KEEP_PREVIEWS = 24


def _prune_previews(preview_dir: Path) -> None:
    """Keep the newest few samples; drop the rest. Never raises."""
    try:
        files = sorted(
            (
                path for path in preview_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".mp3", ".wav"}
            ),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for stale in files[_KEEP_PREVIEWS:]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("preview prune skipped: %s", exc)


class _PreviewBody(BaseModel):
    connection_id: str | None = None
    provider: str
    voice_id: str
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    region: str | None = None


@router.post("/tts/preview")
async def tts_preview(body: _PreviewBody):
    """Generate a short deterministic sample. This explicit action may use quota."""
    from backend.api import settings as settings_api
    from backend.services import tts_service
    from backend.services.elevenlabs_tts_service import ElevenLabsAPIError

    connection = {}
    if body.connection_id:
        saved = await settings_api._load_saved_settings()
        connection = settings_api._tts_probe_connection(
            saved,
            settings_api.TTSProbeRequest(
                connection_id=body.connection_id,
                provider=body.provider,
            ),
        ) if saved else {}

    provider_name = (
        (body.provider or "").strip().lower()
        or str(connection.get("provider") or "").strip().lower()
    )
    voice_id = (body.voice_id or connection.get("voice") or "").strip()
    safe_voice = re.sub(r"[^A-Za-z0-9_-]", "", voice_id)
    if not voice_id or not safe_voice:
        raise HTTPException(400, "voice_id is required")
    try:
        provider = tts_service.TTSProvider(provider_name)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown provider: {provider_name}") from exc

    probe = settings_api.TTSProbeRequest(
        connection_id=body.connection_id,
        provider=provider.value,
        api_key=body.api_key,
        base_url=body.base_url or connection.get("base_url"),
        model=body.model or connection.get("model"),
        voice_id=voice_id,
        region=body.region or connection.get("region"),
    )
    api_key = await settings_api._tts_probe_key(probe)
    preview_dir = settings.TMP_DIR / "tts_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    wav_providers = {
        tts_service.TTSProvider.OPENAI_COMPATIBLE,
        tts_service.TTSProvider.AZURE,
        tts_service.TTSProvider.GPT_SOVITS,
    }
    suffix = ".wav" if provider in wav_providers else ".mp3"
    media_type = "audio/wav" if suffix == ".wav" else "audio/mpeg"
    output_path = preview_dir / f"{provider.value}_{safe_voice}_{uuid4().hex[:8]}{suffix}"
    _prune_previews(preview_dir)

    try:
        await tts_service.generate_tts(
            _PREVIEW_TEXT,
            provider=provider,
            voice_id=voice_id,
            api_key=api_key,
            output_path=output_path,
            model=body.model or connection.get("model"),
            base_url=body.base_url or connection.get("base_url"),
            region=body.region or connection.get("region"),
            target_language="vi",
        )
    except ElevenLabsAPIError as exc:
        output_path.unlink(missing_ok=True)
        logger.warning(
            "TTS preview failed: provider=%s kind=%s status=%s voice_suffix=%s",
            provider.value, exc.kind, exc.status_code, safe_voice[-6:],
        )
        raise HTTPException(400, {
            "code": exc.kind,
            "provider_status": exc.status_code,
            "message": str(exc),
        }) from exc
    except (VoiceGenerationError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        logger.warning(
            "TTS preview failed: provider=%s voice_suffix=%s error=%s",
            provider.value, safe_voice[-6:], exc,
        )
        raise HTTPException(400, str(exc)[:200]) from exc
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        logger.warning(
            "TTS preview crashed: provider=%s voice_suffix=%s type=%s",
            provider.value, safe_voice[-6:], type(exc).__name__,
        )
        raise HTTPException(500, "Voice preview failed unexpectedly.") from exc

    return FileResponse(
        output_path,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )
