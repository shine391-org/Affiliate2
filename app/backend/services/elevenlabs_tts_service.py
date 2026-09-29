# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.core.exceptions import VoiceGenerationError


_OFFICIAL_HOST = "api.elevenlabs.io"
_DEFAULT_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsAPIError(VoiceGenerationError):
    """Safe, classified ElevenLabs API failure without credentials/headers."""

    def __init__(self, message: str, *, kind: str, status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


def canonicalize_base_url(base_url: str | None = None) -> str:
    """Normalize the official ElevenLabs API root while preserving proxies.

    Only api.elevenlabs.io receives /v1 normalization. Custom/proxy endpoints are
    left structurally unchanged apart from a trailing slash.
    """
    raw = (base_url or _DEFAULT_BASE_URL).strip().rstrip("/")
    if not raw:
        raw = _DEFAULT_BASE_URL
    parsed = urlsplit(raw)
    if parsed.hostname and parsed.hostname.lower() == _OFFICIAL_HOST:
        parts = [part for part in parsed.path.split("/") if part]
        while parts and parts[0].lower() == "v1":
            parts.pop(0)
        path = "/v1" + ("/" + "/".join(parts) if parts else "")
        return urlunsplit((parsed.scheme or "https", parsed.netloc, path, parsed.query, parsed.fragment)).rstrip("/")
    return raw


def build_api_url(base_url: str | None, resource: str) -> str:
    return f"{canonicalize_base_url(base_url)}/{resource.lstrip('/')}"


def _safe_error_detail(response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message")
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status")
        if detail:
            return str(detail)[:200]
    return str(getattr(response, "text", "") or "provider rejected the request")[:200]


def _raise_for_response(response, *, resource: str) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 400:
        return
    if status in {401, 403}:
        kind = "auth"
        message = "ElevenLabs API key is invalid or lacks permission."
    elif status == 404:
        kind = "not_found"
        message = f"ElevenLabs resource not found ({resource}); check the endpoint, model, and voice ID."
    elif status in {408, 504}:
        kind = "network_timeout"
        message = "ElevenLabs request timed out."
    else:
        kind = "provider_error"
        message = f"ElevenLabs HTTP {status}: {_safe_error_detail(response)}"
    raise ElevenLabsAPIError(message, kind=kind, status_code=status)


async def list_voices(*, api_key: str, base_url: str | None = None) -> list[dict]:
    if not api_key:
        raise ElevenLabsAPIError(
            "ElevenLabs API key is required.", kind="auth", status_code=None,
        )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                build_api_url(base_url, "voices"),
                headers={"xi-api-key": api_key, "Accept": "application/json"},
            )
    except httpx.TimeoutException as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs request timed out.", kind="network_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs is unreachable.", kind="network",
        ) from exc
    _raise_for_response(response, resource="voices")
    try:
        rows = response.json().get("voices") or []
    except Exception as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs returned an invalid voice list.", kind="invalid_response",
        ) from exc
    return [
        {
            "voice_id": str(row.get("voice_id") or ""),
            "name": str(row.get("name") or row.get("voice_id") or "Unnamed voice"),
            "category": row.get("category"),
            "labels": row.get("labels") if isinstance(row.get("labels"), dict) else {},
        }
        for row in rows
        if isinstance(row, dict) and row.get("voice_id")
    ]


async def list_models(*, api_key: str, base_url: str | None = None) -> list[dict]:
    if not api_key:
        raise ElevenLabsAPIError(
            "ElevenLabs API key is required.", kind="auth", status_code=None,
        )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                build_api_url(base_url, "models"),
                headers={"xi-api-key": api_key, "Accept": "application/json"},
            )
    except httpx.TimeoutException as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs request timed out.", kind="network_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs is unreachable.", kind="network",
        ) from exc
    _raise_for_response(response, resource="models")
    try:
        payload = response.json()
    except Exception as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs returned an invalid model list.", kind="invalid_response",
        ) from exc
    rows = payload if isinstance(payload, list) else payload.get("models", [])
    return [
        {
            "model_id": str(row.get("model_id") or ""),
            "name": str(row.get("name") or row.get("model_id") or "Unnamed model"),
        }
        for row in rows
        if isinstance(row, dict) and row.get("model_id")
    ]


async def generate_voice(
    text: str,
    *,
    voice_id: str,
    api_key: str,
    output_path: Path,
    model: str = "eleven_multilingual_v2",
    base_url: str | None = None,
    speed: float = 1.0,
) -> Path:
    if not api_key:
        raise ElevenLabsAPIError(
            "ElevenLabs API key is required.", kind="auth", status_code=None,
        )
    if not voice_id:
        raise ElevenLabsAPIError(
            "ElevenLabs voice ID is required.", kind="voice_missing", status_code=None,
        )
    url = build_api_url(base_url, f"text-to-speech/{voice_id}")
    payload = {
        "text": text,
        "model_id": model or "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": max(0.7, min(1.2, float(speed))),
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs speech generation timed out.", kind="network_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise ElevenLabsAPIError(
            "ElevenLabs is unreachable.", kind="network",
        ) from exc
    _raise_for_response(response, resource=f"text-to-speech/{voice_id}")
    if not response.content:
        raise ElevenLabsAPIError(
            "ElevenLabs returned empty audio.", kind="invalid_response",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return output_path
