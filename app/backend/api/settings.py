# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/settings — user settings CRUD + credential testing + OAuth callbacks."""
import asyncio
import json
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import Optional

from backend.core.localization import (
    DEFAULT_REWRITE_GUARDRAILS,
    DEFAULT_TRANSLATION_GUARDRAILS,
    LocalizationProfile,
)
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.user_settings import UserSettings
from backend.core.crypto import encrypt, decrypt_safe
from backend.config import settings as app_settings
from backend.services.download_browser_session import (
    SUPPORTED_DOWNLOAD_BROWSERS,
    normalize_download_browser,
    normalize_download_profile,
    normalize_douyin_cdp_url,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_AI_PROBE_TIMEOUT_S = 20.0


def _validated_browser_override(browser: str, override: str | None) -> str | None:
    """Use an override only when it still exists and belongs to the selected browser."""
    if not override:
        return None
    from backend.services.download_browser_launcher import resolve_browser_executable
    resolved = resolve_browser_executable(browser, override=override)
    return override if resolved.get("found") else None


def _load_provider_registry(raw: str | None) -> dict[str, dict]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(provider).strip().lower(): dict(config)
        for provider, config in value.items()
        if str(provider).strip() and isinstance(config, dict)
    }


def _save_provider_registry(value: dict[str, dict]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_connection_id(value: str) -> str:
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip()).strip("-_").lower()
    if not cleaned:
        raise ValueError("Connection id/name cannot be empty.")
    return cleaned[:96]


def _load_ai_connections(settings_row) -> dict[str, dict]:
    """Load v2 connection registry, lazily projecting legacy provider snapshots."""
    raw = _load_provider_registry(getattr(settings_row, "ai_connections_json", None))
    connections: dict[str, dict] = {}
    for connection_id, cfg in raw.items():
        provider = str(cfg.get("provider") or "").strip().lower()
        if not provider:
            continue
        connections[connection_id] = {
            "label": str(cfg.get("label") or connection_id),
            "provider": provider,
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "api_key_encrypted": cfg.get("api_key_encrypted"),
            "archived": bool(cfg.get("archived")),
        }

    if not connections:
        legacy = _load_provider_registry(
            getattr(settings_row, "ai_provider_profiles_json", None)
        )
        for provider, cfg in legacy.items():
            connection_id = _safe_connection_id(provider)
            connections[connection_id] = {
                "label": provider,
                "provider": provider,
                "model": cfg.get("model"),
                "base_url": cfg.get("base_url"),
                "api_key_encrypted": cfg.get("api_key_encrypted"),
            }

    active_provider = (getattr(settings_row, "ai_provider", None) or "").strip().lower()
    active_id = (getattr(settings_row, "ai_connection_id", None) or "").strip().lower()
    if active_provider:
        candidate = active_id or _safe_connection_id(active_provider)
        existing = connections.get(candidate, {})
        connections[candidate] = {
            "label": existing.get("label") or active_provider,
            "provider": active_provider,
            "model": getattr(settings_row, "ai_model", None),
            "base_url": getattr(settings_row, "ai_base_url", None),
            "api_key_encrypted": getattr(settings_row, "ai_api_key_encrypted", None),
            "archived": False,
        }
    return connections


def _load_tts_connections(settings_row) -> dict[str, dict]:
    raw = _load_provider_registry(getattr(settings_row, "tts_connections_json", None))
    connections: dict[str, dict] = {}
    for connection_id, cfg in raw.items():
        provider = str(cfg.get("provider") or "").strip().lower()
        if not provider:
            continue
        connections[connection_id] = {
            "label": str(cfg.get("label") or connection_id),
            "provider": provider,
            "voice": cfg.get("voice"),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "region": cfg.get("region"),
            "api_key_encrypted": cfg.get("api_key_encrypted"),
            "archived": bool(cfg.get("archived")),
        }

    if not connections:
        legacy = _load_provider_registry(
            getattr(settings_row, "tts_provider_profiles_json", None)
        )
        for provider, cfg in legacy.items():
            connection_id = _safe_connection_id(provider)
            connections[connection_id] = {
                "label": provider,
                "provider": provider,
                "voice": cfg.get("voice"),
                "model": cfg.get("model"),
                "base_url": cfg.get("base_url"),
                "region": cfg.get("region"),
                "api_key_encrypted": cfg.get("api_key_encrypted"),
            }

    active_provider = (getattr(settings_row, "tts_provider", None) or "").strip().lower()
    active_id = (getattr(settings_row, "tts_connection_id", None) or "").strip().lower()
    if active_provider:
        candidate = active_id or _safe_connection_id(active_provider)
        existing = connections.get(candidate, {})
        connections[candidate] = {
            "label": existing.get("label") or active_provider,
            "provider": active_provider,
            "voice": getattr(settings_row, "preferred_tts_voice", None),
            "model": getattr(settings_row, "tts_model", None),
            "base_url": getattr(settings_row, "tts_base_url", None),
            "region": getattr(settings_row, "tts_region", None),
            "api_key_encrypted": getattr(settings_row, "tts_api_key_encrypted", None),
            "archived": False,
        }
    return connections


def _public_ai_connections(settings_row) -> dict[str, dict]:
    return {
        connection_id: {
            "id": connection_id,
            "label": cfg.get("label") or connection_id,
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "api_key_set": bool(cfg.get("api_key_encrypted")),
        }
        for connection_id, cfg in _load_ai_connections(settings_row).items()
        if not cfg.get("archived")
    }


def _public_tts_connections(settings_row) -> dict[str, dict]:
    return {
        connection_id: {
            "id": connection_id,
            "label": cfg.get("label") or connection_id,
            "provider": cfg.get("provider"),
            "voice": cfg.get("voice"),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "region": cfg.get("region"),
            "api_key_set": bool(cfg.get("api_key_encrypted")),
        }
        for connection_id, cfg in _load_tts_connections(settings_row).items()
        if not cfg.get("archived")
    }


def _active_ai_connection_id(settings_row) -> str | None:
    connections = _load_ai_connections(settings_row)
    explicit = (getattr(settings_row, "ai_connection_id", None) or "").strip().lower()
    if explicit in connections and not connections[explicit].get("archived"):
        return explicit
    provider = (getattr(settings_row, "ai_provider", None) or "").strip().lower()
    candidate = _safe_connection_id(provider) if provider else None
    return (
        candidate
        if candidate in connections and not connections[candidate].get("archived")
        else None
    )


def _active_tts_connection_id(settings_row) -> str | None:
    connections = _load_tts_connections(settings_row)
    explicit = (getattr(settings_row, "tts_connection_id", None) or "").strip().lower()
    if explicit in connections and not connections[explicit].get("archived"):
        return explicit
    provider = (getattr(settings_row, "tts_provider", None) or "").strip().lower()
    candidate = _safe_connection_id(provider) if provider else None
    return (
        candidate
        if candidate in connections and not connections[candidate].get("archived")
        else None
    )


def _capture_ai_provider(registry: dict[str, dict], settings_row) -> None:
    provider = (getattr(settings_row, "ai_provider", None) or "").strip().lower()
    if not provider:
        return
    registry[provider] = {
        "model": getattr(settings_row, "ai_model", None),
        "base_url": getattr(settings_row, "ai_base_url", None),
        "api_key_encrypted": getattr(settings_row, "ai_api_key_encrypted", None),
    }


def _capture_tts_provider(registry: dict[str, dict], settings_row) -> None:
    provider = (getattr(settings_row, "tts_provider", None) or "").strip().lower()
    if not provider:
        return
    registry[provider] = {
        "voice": getattr(settings_row, "preferred_tts_voice", None),
        "model": getattr(settings_row, "tts_model", None),
        "base_url": getattr(settings_row, "tts_base_url", None),
        "region": getattr(settings_row, "tts_region", None),
        "api_key_encrypted": getattr(settings_row, "tts_api_key_encrypted", None),
    }


def _public_ai_provider_profiles(settings_row) -> dict[str, dict]:
    registry = _load_provider_registry(getattr(settings_row, "ai_provider_profiles_json", None))
    _capture_ai_provider(registry, settings_row)
    return {
        provider: {
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "api_key_set": bool(cfg.get("api_key_encrypted")),
        }
        for provider, cfg in registry.items()
    }


def _public_tts_provider_profiles(settings_row) -> dict[str, dict]:
    registry = _load_provider_registry(getattr(settings_row, "tts_provider_profiles_json", None))
    _capture_tts_provider(registry, settings_row)
    return {
        provider: {
            "voice": cfg.get("voice"),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "region": cfg.get("region"),
            "api_key_set": bool(cfg.get("api_key_encrypted")),
        }
        for provider, cfg in registry.items()
    }


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    # AI provider preference (BYOK)
    ai_provider: str = "openai"
    ai_model: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_api_key_set: bool = False  # masked — never returns the actual key
    ai_provider_profiles: dict[str, dict] = Field(default_factory=dict)
    ai_connection_id: Optional[str] = None
    ai_connections: dict[str, dict] = Field(default_factory=dict)

    # Service API keys (BYOK) — booleans only, never the plaintext key
    youtube_api_key_set: bool = False
    huggingface_token_set: bool = False
    meta_page_access_token_set: bool = False

    # OAuth / browser-session status
    download_cookie_browser: str = "auto"
    download_cookie_profile: Optional[str] = None
    download_cookie_executable_path: Optional[str] = None
    douyin_browser: str = "edge"
    douyin_executable_path: Optional[str] = None
    douyin_cdp_url: str = "http://127.0.0.1:9222"
    youtube_channel_title: Optional[str] = None
    youtube_connected: bool = False
    tiktok_upload_connected: bool = False
    tiktok_legacy_cookie_present: bool = False
    facebook_page_connected: bool = False
    meta_page_id: Optional[str] = None
    meta_page_name: Optional[str] = None
    instagram_connected: bool = False
    instagram_user_id: Optional[str] = None

    # Preferences
    preferred_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    tts_provider: str = "edge_tts"
    preferred_tts_voice: Optional[str] = None
    tts_model: Optional[str] = None
    tts_base_url: Optional[str] = None
    tts_region: Optional[str] = None
    tts_api_key_set: bool = False
    tts_provider_profiles: dict[str, dict] = Field(default_factory=dict)
    tts_connection_id: Optional[str] = None
    tts_connections: dict[str, dict] = Field(default_factory=dict)
    caption_style: str = "viral"
    caption_enabled: bool = True
    music_enabled: bool = True
    music_genre: str = "lofi"
    music_volume_db: float = -14.0
    upload_to_youtube: bool = True
    upload_to_tiktok: bool = False
    tiktok_default_privacy: str = "PUBLIC_TO_EVERYONE"

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    # AI provider preference (BYOK)
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_api_key: Optional[str] = None  # plaintext on input — encrypted before storage
    ai_connection_id: Optional[str] = None

    # Service API keys (BYOK) — plaintext on input, encrypted before storage.
    # Send empty string to clear a stored key.
    youtube_api_key: Optional[str] = None
    huggingface_token: Optional[str] = None
    meta_page_access_token: Optional[str] = None
    meta_page_id: Optional[str] = None

    # Canonical yt-dlp browser session. Cookies are never copied into this
    # request/model; only the local browser/profile selector is persisted.
    download_cookie_browser: Optional[str] = None
    download_cookie_profile: Optional[str] = None
    download_cookie_executable_path: Optional[str] = None
    douyin_browser: Optional[str] = None
    douyin_executable_path: Optional[str] = None
    douyin_cdp_url: Optional[str] = None

    # Preferences
    preferred_voice_id: Optional[str] = None
    tts_provider: Optional[str] = None
    preferred_tts_voice: Optional[str] = None
    tts_model: Optional[str] = None
    tts_base_url: Optional[str] = None
    tts_region: Optional[str] = None
    tts_api_key: Optional[str] = None
    tts_connection_id: Optional[str] = None
    caption_style: Optional[str] = None
    caption_enabled: Optional[bool] = None
    music_enabled: Optional[bool] = None
    music_genre: Optional[str] = None
    music_volume_db: Optional[float] = None
    upload_to_youtube: Optional[bool] = None
    upload_to_tiktok: Optional[bool] = None
    tiktok_default_privacy: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Return user settings with secrets masked."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()

    if not s:
        return SettingsResponse()

    return SettingsResponse(
        ai_provider=s.ai_provider or "openai",
        ai_model=s.ai_model,
        ai_base_url=s.ai_base_url,
        ai_api_key_set=bool(s.ai_api_key_encrypted),
        ai_provider_profiles=_public_ai_provider_profiles(s),
        ai_connection_id=_active_ai_connection_id(s),
        ai_connections=_public_ai_connections(s),
        youtube_api_key_set=bool(s.youtube_api_key_encrypted),
        huggingface_token_set=bool(s.huggingface_token_encrypted),
        meta_page_access_token_set=bool(getattr(s, "meta_page_access_token_encrypted", None)),
        download_cookie_browser=normalize_download_browser(s.download_cookie_browser),
        download_cookie_profile=normalize_download_profile(s.download_cookie_profile),
        download_cookie_executable_path=getattr(s, "download_cookie_executable_path", None),
        douyin_browser=normalize_download_browser(getattr(s, "douyin_browser", None) or "edge"),
        douyin_executable_path=getattr(s, "douyin_executable_path", None),
        douyin_cdp_url=normalize_douyin_cdp_url(getattr(s, "douyin_cdp_url", None)),
        youtube_channel_title=s.youtube_channel_title,
        youtube_connected=bool(s.youtube_credentials_json_encrypted),
        # Canonical Publish is connected only when the official OAuth token is
        # present. A legacy cookie may still exist for migration visibility but
        # must not make Direct Post look ready.
        tiktok_upload_connected=bool(s.tiktok_upload_token_encrypted),
        tiktok_legacy_cookie_present=bool(s.tiktok_cookie_encrypted),
        facebook_page_connected=bool(
            getattr(s, "meta_page_access_token_encrypted", None)
            and getattr(s, "meta_page_id", None)
        ),
        meta_page_id=getattr(s, "meta_page_id", None),
        meta_page_name=getattr(s, "meta_page_name", None),
        instagram_connected=bool(
            getattr(s, "meta_page_access_token_encrypted", None)
            and getattr(s, "instagram_user_id", None)
        ),
        instagram_user_id=getattr(s, "instagram_user_id", None),
        preferred_voice_id=s.preferred_voice_id or "21m00Tcm4TlvDq8ikWAM",
        tts_provider=s.tts_provider or "edge_tts",
        preferred_tts_voice=s.preferred_tts_voice,
        tts_model=s.tts_model,
        tts_base_url=s.tts_base_url,
        tts_region=s.tts_region,
        tts_api_key_set=bool(s.tts_api_key_encrypted),
        tts_provider_profiles=_public_tts_provider_profiles(s),
        tts_connection_id=_active_tts_connection_id(s),
        tts_connections=_public_tts_connections(s),
        caption_style=s.caption_style or "viral",
        caption_enabled=s.caption_enabled if s.caption_enabled is not None else True,
        music_enabled=s.music_enabled if s.music_enabled is not None else True,
        music_genre=s.music_genre or "lofi",
        music_volume_db=s.music_volume_db if s.music_volume_db is not None else -14.0,
        upload_to_youtube=s.upload_to_youtube if s.upload_to_youtube is not None else True,
        upload_to_tiktok=s.upload_to_tiktok or False,
        tiktok_default_privacy=s.tiktok_default_privacy or "PUBLIC_TO_EVERYONE",
    )


@router.post("/settings", response_model=SettingsResponse)
async def update_settings(update: SettingsUpdate):
    """Update user settings. Keys are encrypted before saving."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
        if not s:
            s = UserSettings(user_id="local")
            db.add(s)

        # Stable connection registry supports multiple endpoints/accounts using
        # the same provider transport. The legacy active provider columns remain
        # as the runtime compatibility slot.
        if update.ai_connection_id is not None:
            connection_id = _safe_connection_id(update.ai_connection_id)
            connections = _load_ai_connections(s)
            selected = connections.get(connection_id)
            if not selected:
                raise HTTPException(status_code=400, detail=f"Unknown AI connection: {connection_id}")
            if selected.get("archived"):
                raise HTTPException(
                    status_code=409,
                    detail=f"AI connection '{connection_id}' is archived and cannot be activated.",
                )
            requested_provider = (update.ai_provider or "").strip().lower()
            selected_provider = str(selected.get("provider") or "").strip().lower()
            if requested_provider and requested_provider != selected_provider:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"AI connection '{connection_id}' is {selected_provider}; "
                        f"request also supplied conflicting provider {requested_provider}."
                    ),
                )
            s.ai_connection_id = connection_id
            s.ai_provider = selected.get("provider")
            s.ai_model = selected.get("model")
            s.ai_base_url = selected.get("base_url")
            s.ai_api_key_encrypted = selected.get("api_key_encrypted")

        # AI provider connections are persisted per provider. The active row
        # remains the runtime slot, while the registry remembers previously
        # entered model/base/key so switching providers restores the old setup
        # instead of presenting a blank form.
        ai_registry = _load_provider_registry(
            getattr(s, "ai_provider_profiles_json", None)
        )
        _capture_ai_provider(ai_registry, s)

        if update.ai_provider is not None:
            new_provider = update.ai_provider.strip().lower()
            old_provider = (s.ai_provider or "").strip().lower()
            if new_provider != old_provider and update.ai_connection_id is None:
                # Provider-only switching is a legacy API. It is safe only when
                # that provider maps to at most one live connection; otherwise
                # provider name is no longer sufficient identity.
                connection_matches = [
                    (cid, cfg)
                    for cid, cfg in _load_ai_connections(s).items()
                    if not cfg.get("archived")
                    and str(cfg.get("provider") or "").strip().lower() == new_provider
                ]
                if len(connection_matches) > 1:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Multiple AI connections use provider '{new_provider}'. "
                            "Select ai_connection_id explicitly."
                        ),
                    )
                if len(connection_matches) == 1:
                    cid, selected = connection_matches[0]
                    s.ai_connection_id = cid
                    s.ai_provider = new_provider
                    s.ai_model = selected.get("model")
                    s.ai_base_url = selected.get("base_url")
                    s.ai_api_key_encrypted = selected.get("api_key_encrypted")
                else:
                    saved = ai_registry.get(new_provider, {})
                    s.ai_provider = new_provider
                    s.ai_model = saved.get("model")
                    s.ai_base_url = saved.get("base_url")
                    s.ai_api_key_encrypted = saved.get("api_key_encrypted")
                    s.ai_connection_id = _safe_connection_id(new_provider)
            else:
                s.ai_provider = new_provider
        if update.ai_model is not None:
            s.ai_model = update.ai_model or None
        if update.ai_base_url is not None:
            s.ai_base_url = update.ai_base_url.strip() or None
        if update.ai_api_key is not None:
            s.ai_api_key_encrypted = encrypt(update.ai_api_key) if update.ai_api_key else None
        _capture_ai_provider(ai_registry, s)
        s.ai_provider_profiles_json = _save_provider_registry(ai_registry)
        ai_connections = _load_ai_connections(s)
        active_ai_id = (
            (getattr(s, "ai_connection_id", None) or "").strip().lower()
            or _safe_connection_id((s.ai_provider or "openai"))
        )
        s.ai_connection_id = active_ai_id
        previous_ai = ai_connections.get(active_ai_id, {})
        ai_connections[active_ai_id] = {
            "label": previous_ai.get("label") or active_ai_id,
            "provider": (s.ai_provider or "openai").strip().lower(),
            "model": s.ai_model,
            "base_url": s.ai_base_url,
            "api_key_encrypted": s.ai_api_key_encrypted,
        }
        s.ai_connections_json = _save_provider_registry(ai_connections)

        # Service API keys (BYOK) — empty string clears, populated value encrypts
        if update.youtube_api_key is not None:
            s.youtube_api_key_encrypted = encrypt(update.youtube_api_key) if update.youtube_api_key else None
        if update.huggingface_token is not None:
            s.huggingface_token_encrypted = encrypt(update.huggingface_token) if update.huggingface_token else None
        if update.meta_page_access_token is not None:
            s.meta_page_access_token_encrypted = (
                encrypt(update.meta_page_access_token)
                if update.meta_page_access_token else None
            )
            if not update.meta_page_access_token:
                s.meta_page_name = None
                s.instagram_user_id = None
        if update.meta_page_id is not None:
            s.meta_page_id = update.meta_page_id.strip() or None
            s.meta_page_name = None
            s.instagram_user_id = None

        if update.download_cookie_browser is not None:
            try:
                s.download_cookie_browser = normalize_download_browser(update.download_cookie_browser)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"{exc}. Choose one of: {', '.join(SUPPORTED_DOWNLOAD_BROWSERS)}",
                )
        if update.download_cookie_profile is not None:
            s.download_cookie_profile = normalize_download_profile(update.download_cookie_profile)
        if update.download_cookie_executable_path is not None:
            override = update.download_cookie_executable_path.strip() or None
            if override:
                from backend.services.download_browser_launcher import resolve_browser_executable
                selected_cookie_browser = normalize_download_browser(
                    update.download_cookie_browser or s.download_cookie_browser
                )
                if selected_cookie_browser == "auto":
                    raise HTTPException(status_code=400, detail="Choose a concrete cookie browser before saving an executable override")
                resolution = resolve_browser_executable(selected_cookie_browser, override)
                if not resolution["found"]:
                    raise HTTPException(status_code=400, detail=resolution["error"])
            s.download_cookie_executable_path = override
        if update.douyin_browser is not None:
            try:
                douyin_browser = normalize_download_browser(update.douyin_browser)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if douyin_browser not in {"edge", "chrome", "brave"}:
                raise HTTPException(status_code=400, detail="Douyin browser must be Edge, Chrome, or Brave")
            s.douyin_browser = douyin_browser
        if update.douyin_executable_path is not None:
            override = update.douyin_executable_path.strip() or None
            if override:
                from backend.services.download_browser_launcher import resolve_browser_executable
                selected_douyin_browser = normalize_download_browser(
                    update.douyin_browser or getattr(s, "douyin_browser", None) or "edge"
                )
                resolution = resolve_browser_executable(selected_douyin_browser, override)
                if not resolution["found"]:
                    raise HTTPException(status_code=400, detail=resolution["error"])
            s.douyin_executable_path = override
        if update.douyin_cdp_url is not None:
            try:
                s.douyin_cdp_url = normalize_douyin_cdp_url(update.douyin_cdp_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Preference fields
        if update.preferred_voice_id is not None:
            s.preferred_voice_id = update.preferred_voice_id
        if update.tts_connection_id is not None:
            connection_id = _safe_connection_id(update.tts_connection_id)
            connections = _load_tts_connections(s)
            selected = connections.get(connection_id)
            if not selected:
                raise HTTPException(status_code=400, detail=f"Unknown TTS connection: {connection_id}")
            if selected.get("archived"):
                raise HTTPException(
                    status_code=409,
                    detail=f"TTS connection '{connection_id}' is archived and cannot be activated.",
                )
            requested_provider = (update.tts_provider or "").strip().lower()
            selected_provider = str(selected.get("provider") or "").strip().lower()
            if requested_provider and requested_provider != selected_provider:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"TTS connection '{connection_id}' is {selected_provider}; "
                        f"request also supplied conflicting provider {requested_provider}."
                    ),
                )
            s.tts_connection_id = connection_id
            s.tts_provider = selected.get("provider")
            s.preferred_tts_voice = selected.get("voice")
            s.tts_model = selected.get("model")
            s.tts_base_url = selected.get("base_url")
            s.tts_region = selected.get("region")
            s.tts_api_key_encrypted = selected.get("api_key_encrypted")

        tts_registry = _load_provider_registry(
            getattr(s, "tts_provider_profiles_json", None)
        )
        _capture_tts_provider(tts_registry, s)

        if update.tts_provider is not None:
            new_tts_provider = update.tts_provider.strip().lower()
            old_tts_provider = (s.tts_provider or "").strip().lower()
            if new_tts_provider != old_tts_provider and update.tts_connection_id is None:
                connection_matches = [
                    (cid, cfg)
                    for cid, cfg in _load_tts_connections(s).items()
                    if not cfg.get("archived")
                    and str(cfg.get("provider") or "").strip().lower() == new_tts_provider
                ]
                if len(connection_matches) > 1:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Multiple TTS connections use provider '{new_tts_provider}'. "
                            "Select tts_connection_id explicitly."
                        ),
                    )
                if len(connection_matches) == 1:
                    cid, selected = connection_matches[0]
                    s.tts_connection_id = cid
                    s.tts_provider = new_tts_provider
                    s.preferred_tts_voice = selected.get("voice")
                    s.tts_model = selected.get("model")
                    s.tts_base_url = selected.get("base_url")
                    s.tts_region = selected.get("region")
                    s.tts_api_key_encrypted = selected.get("api_key_encrypted")
                else:
                    saved = tts_registry.get(new_tts_provider, {})
                    s.tts_provider = new_tts_provider
                    s.preferred_tts_voice = saved.get("voice")
                    s.tts_model = saved.get("model")
                    s.tts_base_url = saved.get("base_url")
                    s.tts_region = saved.get("region")
                    s.tts_api_key_encrypted = saved.get("api_key_encrypted")
                    s.tts_connection_id = _safe_connection_id(new_tts_provider)
            else:
                s.tts_provider = new_tts_provider
        if update.preferred_tts_voice is not None:
            s.preferred_tts_voice = update.preferred_tts_voice or None
        if update.tts_model is not None:
            s.tts_model = update.tts_model.strip() or None
        if update.tts_base_url is not None:
            candidate_tts_base_url = update.tts_base_url.strip() or None
            effective_tts_provider = (
                update.tts_provider or s.tts_provider or "edge_tts"
            ).strip().lower()
            if effective_tts_provider == "elevenlabs":
                from backend.services.elevenlabs_tts_service import canonicalize_base_url
                candidate_tts_base_url = canonicalize_base_url(candidate_tts_base_url)
            s.tts_base_url = candidate_tts_base_url
        if update.tts_region is not None:
            s.tts_region = update.tts_region.strip() or None
        if update.tts_api_key is not None:
            s.tts_api_key_encrypted = encrypt(update.tts_api_key) if update.tts_api_key else None
        _capture_tts_provider(tts_registry, s)
        s.tts_provider_profiles_json = _save_provider_registry(tts_registry)
        tts_connections = _load_tts_connections(s)
        active_tts_id = (
            (getattr(s, "tts_connection_id", None) or "").strip().lower()
            or _safe_connection_id((s.tts_provider or "edge_tts"))
        )
        s.tts_connection_id = active_tts_id
        previous_tts = tts_connections.get(active_tts_id, {})
        tts_connections[active_tts_id] = {
            "label": previous_tts.get("label") or active_tts_id,
            "provider": (s.tts_provider or "edge_tts").strip().lower(),
            "voice": s.preferred_tts_voice,
            "model": s.tts_model,
            "base_url": s.tts_base_url,
            "region": s.tts_region,
            "api_key_encrypted": s.tts_api_key_encrypted,
        }
        s.tts_connections_json = _save_provider_registry(tts_connections)

        if update.caption_style is not None:
            s.caption_style = update.caption_style
        if update.caption_enabled is not None:
            s.caption_enabled = update.caption_enabled
        if update.music_enabled is not None:
            s.music_enabled = update.music_enabled
        if update.music_genre is not None:
            s.music_genre = update.music_genre
        if update.music_volume_db is not None:
            s.music_volume_db = update.music_volume_db
        if update.upload_to_youtube is not None:
            s.upload_to_youtube = update.upload_to_youtube
        if update.upload_to_tiktok is not None:
            s.upload_to_tiktok = update.upload_to_tiktok
        if update.tiktok_default_privacy is not None:
            s.tiktok_default_privacy = update.tiktok_default_privacy

        await db.commit()

    return await get_settings()


class AIConnectionRequest(BaseModel):
    connection_id: Optional[str] = None
    label: str
    provider: str
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    activate: bool = True


class TTSConnectionRequest(BaseModel):
    connection_id: Optional[str] = None
    label: str
    provider: str
    voice: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    region: Optional[str] = None
    api_key: Optional[str] = None
    activate: bool = True


def _profile_references_connection(settings_row, connection_id: str, *, tts: bool = False) -> bool:
    try:
        payload = json.loads(getattr(settings_row, "localization_profiles_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    for profile in payload.get("profiles", []) if isinstance(payload, dict) else []:
        if not isinstance(profile, dict):
            continue
        if tts:
            if str(profile.get("tts_connection_id") or "").strip().lower() == connection_id:
                return True
            if str(profile.get("tts_provider") or "").strip().lower() == connection_id:
                return True
            continue
        for key in ("translation_routes", "rewrite_routes"):
            for route in profile.get(key, []) or []:
                if isinstance(route, dict) and str(route.get("connection_id") or "").strip().lower() == connection_id:
                    return True
        for key in (
            "translation_provider", "translation_fallback_provider",
            "rewrite_provider", "rewrite_fallback_provider",
        ):
            if str(profile.get(key) or "").strip().lower() == connection_id:
                return True
    return False


@router.post("/settings/ai/connections", response_model=SettingsResponse)
async def upsert_ai_connection(body: AIConnectionRequest):
    from backend.core.ai_provider import make_ai_client

    connection_id = _safe_connection_id(body.connection_id or body.label or body.provider)
    provider = (body.provider or "").strip().lower()
    # Validate the transport/config shape before storing it. A blank key is
    # allowed only when an existing encrypted key is being preserved.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        s = result.scalar_one_or_none()
        if not s:
            s = UserSettings(user_id="local")
            db.add(s)
        connections = _load_ai_connections(s)
        if body.connection_id is None and connection_id in connections and not connections[connection_id].get("archived"):
            raise HTTPException(
                status_code=409,
                detail=f"AI connection id '{connection_id}' already exists. Choose another name or edit the existing connection.",
            )
        existing = connections.get(connection_id, {})
        if existing.get("archived"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"AI connection id '{connection_id}' is archived and reserved "
                    "for existing jobs. Create a new connection ID instead of "
                    "reusing archived identity."
                ),
            )
        provider_changed = bool(
            existing.get("provider")
            and str(existing.get("provider")).strip().lower() != provider
        )
        if provider_changed:
            # connection_id is durable identity referenced by profiles and
            # persisted jobs. Reinterpreting the same id as another transport
            # could make an old job consume a different credential/provider.
            # Create a new connection instead.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"AI connection '{connection_id}' is already "
                    f"{existing.get('provider')}. Provider/transport is immutable; "
                    "create a new connection ID for a different provider."
                ),
            )
        encrypted = existing.get("api_key_encrypted")
        if body.api_key is not None:
            encrypted = encrypt(body.api_key) if body.api_key else None

        # Edit semantics are PATCH-like even though this endpoint is POST:
        # omitted optional fields preserve the existing connection, while an
        # explicitly supplied null/blank value clears that field.
        fields_set = body.model_fields_set
        model = (
            existing.get("model")
            if "model" not in fields_set
            else (body.model.strip() if body.model and body.model.strip() else None)
        )
        base_url = (
            existing.get("base_url")
            if "base_url" not in fields_set
            else (body.base_url.strip() if body.base_url and body.base_url.strip() else None)
        )
        key = decrypt_safe(encrypted) if encrypted else ""
        try:
            make_ai_client(
                provider_name=provider,
                api_key=key or "",
                model=model,
                base_url=base_url,
            )
        except Exception as exc:
            # Missing key is permitted while editing an already-stored keyed
            # provider only when that stored key exists.
            if not encrypted:
                raise HTTPException(status_code=400, detail=str(exc)[:240]) from exc

        connections[connection_id] = {
            "label": body.label.strip() or connection_id,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key_encrypted": encrypted,
            "archived": False,
        }
        s.ai_connections_json = _save_provider_registry(connections)
        if body.activate:
            cfg = connections[connection_id]
            s.ai_connection_id = connection_id
            s.ai_provider = provider
            s.ai_model = cfg.get("model")
            s.ai_base_url = cfg.get("base_url")
            s.ai_api_key_encrypted = cfg.get("api_key_encrypted")
        await db.commit()
    return await get_settings()


@router.delete("/settings/ai/connections/{connection_id}", response_model=SettingsResponse)
async def delete_ai_connection(connection_id: str):
    connection_id = _safe_connection_id(connection_id)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        s = result.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Settings not found")
        if _active_ai_connection_id(s) == connection_id:
            raise HTTPException(status_code=409, detail="Activate another AI connection before deleting this one.")
        if _profile_references_connection(s, connection_id):
            raise HTTPException(status_code=409, detail="This AI connection is still referenced by a localization profile.")
        connections = _load_ai_connections(s)
        if connection_id not in connections:
            raise HTTPException(status_code=404, detail="AI connection not found")
        archived = dict(connections[connection_id])
        archived["archived"] = True
        connections[connection_id] = archived
        s.ai_connections_json = _save_provider_registry(connections)
        await db.commit()
    return await get_settings()


@router.post("/settings/tts/connections", response_model=SettingsResponse)
async def upsert_tts_connection(body: TTSConnectionRequest):
    from backend.services import tts_service
    from backend.services.tts_service import TTSProvider

    connection_id = _safe_connection_id(body.connection_id or body.label or body.provider)
    provider = (body.provider or "").strip().lower()
    try:
        TTSProvider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported TTS provider: {provider}") from exc

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        s = result.scalar_one_or_none()
        if not s:
            s = UserSettings(user_id="local")
            db.add(s)
        connections = _load_tts_connections(s)
        if body.connection_id is None and connection_id in connections and not connections[connection_id].get("archived"):
            raise HTTPException(
                status_code=409,
                detail=f"TTS connection id '{connection_id}' already exists. Choose another name or edit the existing connection.",
            )
        existing = connections.get(connection_id, {})
        if existing.get("archived"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"TTS connection id '{connection_id}' is archived and reserved "
                    "for existing jobs. Create a new connection ID instead of "
                    "reusing archived identity."
                ),
            )
        provider_changed = bool(
            existing.get("provider")
            and str(existing.get("provider")).strip().lower() != provider
        )
        if provider_changed:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"TTS connection '{connection_id}' is already "
                    f"{existing.get('provider')}. Provider/transport is immutable; "
                    "create a new connection ID for a different provider."
                ),
            )
        encrypted = existing.get("api_key_encrypted")
        if body.api_key is not None:
            encrypted = encrypt(body.api_key) if body.api_key else None

        fields_set = body.model_fields_set
        voice = (
            existing.get("voice")
            if "voice" not in fields_set
            else (body.voice.strip() if body.voice and body.voice.strip() else None)
        )
        model = (
            existing.get("model")
            if "model" not in fields_set
            else (body.model.strip() if body.model and body.model.strip() else None)
        )
        base_url = (
            existing.get("base_url")
            if "base_url" not in fields_set
            else (body.base_url.strip() if body.base_url and body.base_url.strip() else None)
        )
        region = (
            existing.get("region")
            if "region" not in fields_set
            else (body.region.strip() if body.region and body.region.strip() else None)
        )
        info = tts_service.PROVIDER_INFO[TTSProvider(provider)]
        if info.get("needs_key") and not encrypted:
            raise HTTPException(
                status_code=400,
                detail=f"{info.get('label') or provider} requires its own API key.",
            )
        if provider in {"openai_compatible_tts", "gpt_sovits"} and not base_url:
            raise HTTPException(status_code=400, detail=f"{provider} requires a Base URL.")
        if provider == "azure_tts" and not (region or base_url):
            raise HTTPException(status_code=400, detail="Azure Speech requires a region or custom endpoint.")
        if provider == "elevenlabs" and not voice:
            raise HTTPException(status_code=400, detail="ElevenLabs requires a voice ID.")

        connections[connection_id] = {
            "label": body.label.strip() or connection_id,
            "provider": provider,
            "voice": voice,
            "model": model,
            "base_url": base_url,
            "region": region,
            "api_key_encrypted": encrypted,
            "archived": False,
        }
        s.tts_connections_json = _save_provider_registry(connections)
        if body.activate:
            cfg = connections[connection_id]
            s.tts_connection_id = connection_id
            s.tts_provider = provider
            s.preferred_tts_voice = cfg.get("voice")
            s.tts_model = cfg.get("model")
            s.tts_base_url = cfg.get("base_url")
            s.tts_region = cfg.get("region")
            s.tts_api_key_encrypted = cfg.get("api_key_encrypted")
        await db.commit()
    return await get_settings()


@router.delete("/settings/tts/connections/{connection_id}", response_model=SettingsResponse)
async def delete_tts_connection(connection_id: str):
    connection_id = _safe_connection_id(connection_id)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        s = result.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Settings not found")
        if _active_tts_connection_id(s) == connection_id:
            raise HTTPException(status_code=409, detail="Activate another TTS connection before deleting this one.")
        if _profile_references_connection(s, connection_id, tts=True):
            raise HTTPException(status_code=409, detail="This TTS connection is still referenced by a localization profile.")
        connections = _load_tts_connections(s)
        if connection_id not in connections:
            raise HTTPException(status_code=404, detail="TTS connection not found")
        archived = dict(connections[connection_id])
        archived["archived"] = True
        connections[connection_id] = archived
        s.tts_connections_json = _save_provider_registry(connections)
        await db.commit()
    return await get_settings()


# ── OAuth endpoints ──────────────────────────────────────────────────────────


class AIProbeRequest(BaseModel):
    connection_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class TTSProbeRequest(BaseModel):
    connection_id: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    voice_id: Optional[str] = None
    region: Optional[str] = None


async def _load_saved_tts_settings():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        return result.scalar_one_or_none()


async def _load_saved_settings():
    return await _load_saved_tts_settings()


def _ai_probe_connection(saved, body: AIProbeRequest) -> dict:
    if not saved or not body.connection_id:
        return {}
    connection_id = _safe_connection_id(body.connection_id)
    cfg = _load_ai_connections(saved).get(connection_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"AI connection not found: {connection_id}")
    return cfg


def _tts_probe_connection(saved, body: TTSProbeRequest) -> dict:
    if not saved or not body.connection_id:
        return {}
    connection_id = _safe_connection_id(body.connection_id)
    cfg = _load_tts_connections(saved).get(connection_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"TTS connection not found: {connection_id}")
    return cfg


async def _tts_probe_key(body: TTSProbeRequest) -> str:
    """Resolve probe credentials through the same ownership chain as runtime."""
    if body.api_key is not None:
        return body.api_key.strip()
    saved = await _load_saved_tts_settings()
    connection = _tts_probe_connection(saved, body) if saved else {}
    provider = (
        (body.provider or "").strip().lower()
        or str(connection.get("provider") or "").strip().lower()
    )
    if not saved:
        return app_settings.OPENAI_API_KEY if provider == "openai_tts" else ""

    if connection:
        encrypted = connection.get("api_key_encrypted")
    elif (saved.tts_provider or "").strip().lower() == provider:
        encrypted = getattr(saved, "tts_api_key_encrypted", None)
    else:
        profile = _load_provider_registry(
            getattr(saved, "tts_provider_profiles_json", None)
        ).get(provider, {})
        encrypted = profile.get("api_key_encrypted")
    if encrypted:
        return decrypt_safe(encrypted) or ""

    if provider == "openai_tts":
        if (getattr(saved, "ai_provider", None) or "").strip().lower() == "openai":
            ai_encrypted = getattr(saved, "ai_api_key_encrypted", None)
        else:
            ai_profile = _load_provider_registry(
                getattr(saved, "ai_provider_profiles_json", None)
            ).get("openai", {})
            ai_encrypted = ai_profile.get("api_key_encrypted")
        if ai_encrypted:
            return decrypt_safe(ai_encrypted) or ""
        return app_settings.OPENAI_API_KEY or ""

    return ""


async def _probe_key(body: AIProbeRequest) -> str:
    if body.api_key is not None:
        return body.api_key.strip()

    s = await _load_saved_settings()
    connection = _ai_probe_connection(s, body) if s else {}
    provider = (
        (body.provider or "").lower().strip()
        or str(connection.get("provider") or "").lower().strip()
    )
    if provider in {"ollama", "lmstudio", "vllm"}:
        return ""
    if not s:
        return ""
    if connection:
        encrypted = connection.get("api_key_encrypted")
    elif (s.ai_provider or "").lower().strip() == provider:
        encrypted = s.ai_api_key_encrypted
    else:
        profile = _load_provider_registry(
            getattr(s, "ai_provider_profiles_json", None)
        ).get(provider, {})
        encrypted = profile.get("api_key_encrypted")
    return decrypt_safe(encrypted) if encrypted else ""


@router.get("/settings/ai/effective")
async def effective_ai_runtime():
    """Return the provider/model that runtime resolution would actually use."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()

    from backend.core.ai_provider import describe_effective_ai
    return describe_effective_ai(row)


@router.post("/settings/ai/models")
async def discover_ai_models(body: AIProbeRequest):
    """Discover models from OpenAI/OpenRouter/OpenAI-compatible endpoints."""
    from backend.core.ai_provider import (
        COMPATIBLE_PRESETS, compatible_base_url, list_openai_compatible_models,
    )

    saved = await _load_saved_settings()
    connection = _ai_probe_connection(saved, body) if saved else {}
    provider = (
        (body.provider or "").lower().strip()
        or str(connection.get("provider") or "").lower().strip()
    )
    requested_base = body.base_url or connection.get("base_url")
    if provider == "openai":
        base_url = "https://api.openai.com/v1"
    elif provider == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
    elif provider in COMPATIBLE_PRESETS or provider == "compatible":
        base_url = compatible_base_url(provider, requested_base)
    else:
        raise HTTPException(status_code=400, detail="Model discovery is not supported for this provider.")

    if not base_url:
        raise HTTPException(status_code=400, detail="Base URL is required.")
    key = await _probe_key(body)
    if provider in {"openai", "openrouter"} and not key:
        raise HTTPException(status_code=400, detail="API key is required.")

    try:
        models = await list_openai_compatible_models(base_url, key)
        return {"ok": True, "models": models}
    except Exception as exc:
        logger.warning("AI model discovery failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Model discovery failed: {str(exc)[:200]}")


@router.post("/settings/ai/test")
async def test_ai_connection(body: AIProbeRequest):
    """Test the exact provider/base/model values the user is editing."""
    from backend.core.ai_provider import make_ai_client

    saved = await _load_saved_settings()
    connection = _ai_probe_connection(saved, body) if saved else {}
    key = await _probe_key(body)
    provider_name = (body.provider or connection.get("provider") or "").strip().lower()
    model = body.model or connection.get("model")
    base_url = body.base_url or connection.get("base_url")
    try:
        client = make_ai_client(
            provider_name=provider_name,
            api_key=key,
            model=model,
            base_url=base_url,
        )
        # Probe owns its retry/timeout policy just like C6/C7. Do not let an SDK
        # retry loop or a half-open local endpoint make the Settings UI hang.
        client.max_retries = 0
        reply = await asyncio.wait_for(
            client.chat(
                messages=[{"role": "user", "content": "Reply with exactly OK."}],
                max_tokens=8,
            ),
            timeout=_AI_PROBE_TIMEOUT_S,
        )
        return {"ok": True, "response": (reply or "").strip()[:80]}
    except asyncio.TimeoutError as exc:
        logger.warning("AI connection test timed out after %.1fs", _AI_PROBE_TIMEOUT_S)
        raise HTTPException(
            status_code=408,
            detail=f"Connection timed out after {int(_AI_PROBE_TIMEOUT_S)}s.",
        ) from exc
    except Exception as exc:
        logger.warning("AI connection test failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(exc)[:200]}")


@router.post("/settings/tts/test")
async def test_tts_connection(body: TTSProbeRequest):
    """Validate the exact unsaved TTS form values without generating speech."""
    from backend.core.exceptions import VoiceGenerationError
    from backend.services import tts_service
    from backend.services.elevenlabs_tts_service import ElevenLabsAPIError

    saved = await _load_saved_settings()
    connection = _tts_probe_connection(saved, body) if saved else {}
    provider_name = (
        (body.provider or "").strip().lower()
        or str(connection.get("provider") or "").strip().lower()
    )
    try:
        provider = tts_service.TTSProvider(provider_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_provider",
            "message": f"Unsupported TTS provider: {body.provider}",
        }) from exc

    key = await _tts_probe_key(body)
    try:
        return await tts_service.test_tts_connection(
            provider=provider,
            api_key=key,
            base_url=body.base_url or connection.get("base_url"),
            model=body.model or connection.get("model"),
            voice_id=body.voice_id or connection.get("voice"),
            region=body.region or connection.get("region"),
        )
    except ElevenLabsAPIError as exc:
        logger.warning(
            "ElevenLabs connection test failed: kind=%s status=%s",
            exc.kind, exc.status_code,
        )
        raise HTTPException(status_code=400, detail={
            "code": exc.kind,
            "provider_status": exc.status_code,
            "message": str(exc),
        }) from exc
    except (VoiceGenerationError, ValueError) as exc:
        logger.warning("TTS connection test failed: provider=%s error=%s", provider.value, exc)
        raise HTTPException(status_code=400, detail={
            "code": "invalid_configuration",
            "message": str(exc)[:200],
        }) from exc
    except Exception as exc:
        logger.warning("TTS connection test failed: provider=%s type=%s", provider.value, type(exc).__name__)
        raise HTTPException(status_code=400, detail={
            "code": "provider_unreachable",
            "message": "TTS provider is unreachable.",
        }) from exc


@router.post("/settings/tts/voices")
async def discover_tts_voices(body: TTSProbeRequest):
    """Discover voices with unsaved form credentials or the matching saved key."""
    from backend.services import tts_service
    from backend.services.elevenlabs_tts_service import ElevenLabsAPIError

    saved = await _load_saved_settings()
    connection = _tts_probe_connection(saved, body) if saved else {}
    provider_name = (
        (body.provider or "").strip().lower()
        or str(connection.get("provider") or "").strip().lower()
    )
    try:
        provider = tts_service.TTSProvider(provider_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_provider",
            "message": f"Unsupported TTS provider: {body.provider}",
        }) from exc
    key = await _tts_probe_key(body)
    try:
        voices = await tts_service.list_voices(
            provider, key, base_url=body.base_url or connection.get("base_url"),
        )
        models = []
        if provider == tts_service.TTSProvider.ELEVENLABS:
            from backend.services.elevenlabs_tts_service import list_models
            models = await list_models(api_key=key, base_url=body.base_url or connection.get("base_url"))
        return {
            "ok": True,
            "provider": provider.value,
            "voices": voices,
            "models": models,
        }
    except ElevenLabsAPIError as exc:
        logger.warning(
            "ElevenLabs voice discovery failed: kind=%s status=%s",
            exc.kind, exc.status_code,
        )
        raise HTTPException(status_code=400, detail={
            "code": exc.kind,
            "provider_status": exc.status_code,
            "message": str(exc),
        }) from exc


def _localization_profile_payload(store):
    payload = store.model_dump()
    payload["prompt_defaults"] = {
        "translation_guardrails": DEFAULT_TRANSLATION_GUARDRAILS,
        "rewrite_guardrails": DEFAULT_REWRITE_GUARDRAILS,
    }
    return payload


@router.get("/settings/localization-profiles")
async def get_localization_profiles():
    from backend.core.localization import load_profile_store

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
    store = load_profile_store(s.localization_profiles_json if s else None)
    return _localization_profile_payload(store)


@router.post("/settings/localization-profiles")
async def save_localization_profile(profile: LocalizationProfile):
    from backend.core.localization import load_profile_store, upsert_profile

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
        if not s:
            s = UserSettings(user_id="local")
            db.add(s)
        store = load_profile_store(s.localization_profiles_json)

        ai_connections = _load_ai_connections(s)
        route_errors = []
        for stage_name, routes in (
            ("C6", profile.translation_routes),
            ("C7", profile.rewrite_routes),
        ):
            for index, route in enumerate(routes, start=1):
                cfg = ai_connections.get(route.connection_id.strip().lower())
                if not cfg or cfg.get("archived"):
                    route_errors.append(
                        f"{stage_name} route {index}: AI connection '{route.connection_id}' is missing or archived"
                    )
        if profile.tts_connection_id:
            tts_cfg = _load_tts_connections(s).get(
                profile.tts_connection_id.strip().lower()
            )
            if not tts_cfg or tts_cfg.get("archived"):
                route_errors.append(
                    f"TTS connection '{profile.tts_connection_id}' is missing or archived"
                )
        if route_errors:
            raise HTTPException(status_code=400, detail="; ".join(route_errors))

        try:
            store = upsert_profile(store, profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        s.localization_profiles_json = store.model_dump_json()
        await db.commit()
    return _localization_profile_payload(store)


@router.delete("/settings/localization-profiles/{profile_id}")
async def remove_localization_profile(profile_id: str):
    from backend.core.localization import delete_profile, load_profile_store

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
        if not s:
            return _localization_profile_payload(load_profile_store(None))
        store = delete_profile(load_profile_store(s.localization_profiles_json), profile_id)
        s.localization_profiles_json = store.model_dump_json()
        await db.commit()
    return _localization_profile_payload(store)


@router.post("/settings/localization-profiles/{profile_id}/default")
async def make_default_localization_profile(profile_id: str):
    from backend.core.localization import load_profile_store, set_default_profile

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Localization profile not found")
        store = load_profile_store(s.localization_profiles_json)
        try:
            store = set_default_profile(store, profile_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Localization profile not found")
        s.localization_profiles_json = store.model_dump_json()
        await db.commit()
    return _localization_profile_payload(store)


@router.get("/settings/youtube-auth")
async def youtube_auth():
    """Redirect user to Google OAuth for YouTube upload."""
    if not app_settings.YOUTUBE_CLIENT_ID or not app_settings.YOUTUBE_CLIENT_SECRET:
        return {"error": "YouTube OAuth client ID/secret not configured in .env"}
    from backend.services.youtube_uploader import build_youtube_auth_url
    url = build_youtube_auth_url()
    return RedirectResponse(url)


@router.get("/settings/youtube-callback")
async def youtube_callback(code: str = Query(...), state: str = Query(None)):
    """Handle YouTube OAuth callback — exchange code for credentials."""
    from backend.services.youtube_uploader import exchange_youtube_code
    try:
        result = await exchange_youtube_code(code)
        creds_json = result["credentials"]
        channel_title = result.get("channel_title", "")

        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(UserSettings).where(UserSettings.user_id == "local")
            )
            s = res.scalar_one_or_none()
            if not s:
                s = UserSettings(user_id="local")
                db.add(s)
            import json
            s.youtube_credentials_json_encrypted = encrypt(json.dumps(creds_json))
            s.youtube_channel_title = channel_title
            await db.commit()

        return RedirectResponse(f"/?youtube_connected=true&channel={channel_title}")
    except Exception as e:
        logger.error(f"YouTube OAuth callback failed: {e}")
        return RedirectResponse(f"/?youtube_error={str(e)[:200]}")


@router.get("/settings/tiktok-creator-info")
async def tiktok_creator_info():
    """Return current Direct Post capabilities for the connected TikTok creator."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()

    token = (
        decrypt_safe(s.tiktok_upload_token_encrypted)
        if s and s.tiktok_upload_token_encrypted
        else ""
    )
    if not token:
        raise HTTPException(
            status_code=409,
            detail="TikTok OAuth is not connected. Reconnect TikTok before publishing.",
        )

    from backend.core.exceptions import UploadAuthError, UploadError
    from backend.services.tiktok_uploader import get_tiktok_creator_info

    try:
        return await get_tiktok_creator_info(token)
    except UploadAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except UploadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/settings/tiktok-upload-auth")
async def tiktok_upload_auth():
    """Redirect user to TikTok OAuth for upload."""
    if not app_settings.TIKTOK_CLIENT_KEY or not app_settings.TIKTOK_CLIENT_SECRET:
        return {"error": "TikTok client key/secret not configured in .env"}
    from backend.services.tiktok_uploader import build_tiktok_auth_url
    url = build_tiktok_auth_url()
    return RedirectResponse(url)


@router.get("/settings/tiktok-upload-callback")
async def tiktok_upload_callback(code: str = Query(...), state: str = Query(None)):
    """Handle TikTok OAuth callback — exchange code for tokens."""
    from backend.services.tiktok_uploader import exchange_tiktok_code
    try:
        result = await exchange_tiktok_code(code)

        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(UserSettings).where(UserSettings.user_id == "local")
            )
            s = res.scalar_one_or_none()
            if not s:
                s = UserSettings(user_id="local")
                db.add(s)
            s.tiktok_upload_token_encrypted = encrypt(result["access_token"])
            if result.get("refresh_token"):
                s.tiktok_upload_refresh_token_encrypted = encrypt(result["refresh_token"])
            if result.get("expires_in"):
                from datetime import timedelta
                s.tiktok_upload_token_expiry = datetime.utcnow() + timedelta(seconds=result["expires_in"])
            await db.commit()

        return RedirectResponse("/?tiktok_connected=true")
    except Exception as e:
        logger.error(f"TikTok OAuth callback failed: {e}")
        return RedirectResponse(f"/?tiktok_error={str(e)[:200]}")


class MetaPublishProbeRequest(BaseModel):
    access_token: Optional[str] = None
    page_id: Optional[str] = None


@router.post("/settings/meta/test")
async def test_meta_publish(body: MetaPublishProbeRequest):
    """Validate a Meta Page token and discover its linked Instagram account."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()

    token = (body.access_token or "").strip()
    if not token and row and getattr(row, "meta_page_access_token_encrypted", None):
        token = decrypt_safe(row.meta_page_access_token_encrypted) or ""
    page_id = (body.page_id or "").strip() or (
        getattr(row, "meta_page_id", None) if row else None
    )
    if not token:
        raise HTTPException(status_code=400, detail="Meta Page access token is required.")
    if not page_id:
        raise HTTPException(status_code=400, detail="Facebook Page ID is required.")

    from backend.services.meta_uploader import inspect_meta_page
    try:
        info = await inspect_meta_page(page_id=page_id, access_token=token)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Meta connection failed: {str(exc)[:300]}",
        ) from exc

    # Persist only verified identity metadata; plaintext token is persisted only
    # when the caller explicitly supplied it in this request.
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        target = result.scalar_one_or_none()
        if not target:
            target = UserSettings(user_id="local")
            db.add(target)
        if body.access_token is not None:
            target.meta_page_access_token_encrypted = encrypt(token)
        target.meta_page_id = str(info["page_id"])
        target.meta_page_name = info.get("page_name") or None
        target.instagram_user_id = info.get("instagram_user_id") or None
        await db.commit()

    return info


class DownloadSessionProbeRequest(BaseModel):
    browser: Optional[str] = None
    profile: Optional[str] = None


class DownloadSessionHealthRequest(BaseModel):
    # browser/profile are legacy request aliases kept for compatibility.
    browser: Optional[str] = None
    profile: Optional[str] = None
    douyin_browser: Optional[str] = None
    cookie_browser: Optional[str] = None
    cookie_profile: Optional[str] = None
    douyin_cdp_url: Optional[str] = None


class DownloadSessionOpenRequest(BaseModel):
    browser: Optional[str] = None
    douyin_browser: Optional[str] = None
    cookie_browser: Optional[str] = None
    douyin_cdp_url: Optional[str] = None


class CookieBrowserOpenRequest(BaseModel):
    cookie_browser: Optional[str] = None
    cookie_profile: Optional[str] = None


class DownloadSessionRepairRequest(BaseModel):
    scope: str = "all"


class DownloadSessionResetRequest(BaseModel):
    confirm: bool = False
    clear_profile: bool = False


class DownloadExecutablePickerRequest(BaseModel):
    target: str
    browser: str


async def _probe_douyin_cdp(cdp_url: str) -> dict:
    from backend.services.download_browser_launcher import probe_cdp_endpoint

    endpoint = await asyncio.to_thread(probe_cdp_endpoint, cdp_url)
    cdp = {
        "url": cdp_url,
        "endpoint_reachable": bool(endpoint.get("endpoint_reachable")),
        "browser_product": endpoint.get("browser_product"),
        "websocket_connected": False,
        "page_reachable": False,
        "connected": False,
        "authenticated": False,
        "error": endpoint.get("error"),
    }
    try:
        from backend.services.douyin_browser_session import (
            DouyinCdpUnavailableError,
            open_douyin_cdp_session,
        )
        async with open_douyin_cdp_session(
            cdp_url,
            connect_timeout_ms=4_000,
            navigation_timeout_ms=8_000,
        ) as session:
            page = getattr(session, "page", None)
            cdp.update({
                "endpoint_reachable": True,
                "websocket_connected": True,
                "page_reachable": "douyin.com" in str(getattr(page, "url", "")),
                "connected": True,
                "authenticated": True,
                "cookie_count": len(session.cookie_dict),
                "has_ms_token": bool(session.ms_token),
                "user_agent": session.user_agent[:160] if session.user_agent else "",
            })
    except DouyinCdpUnavailableError as exc:
        cdp["connected"] = False
        cdp["error"] = str(exc)[:500]
    except PermissionError as exc:
        message = str(exc)
        cdp["websocket_connected"] = True
        cdp["connected"] = True
        # This exact error is raised only after inspecting a persistent Douyin
        # page. A broader "no authenticated session" error can follow a
        # temporary probe page that has already been closed.
        cdp["page_reachable"] = "not logged in" in message.lower()
        cdp["error"] = message[:500]
    except Exception as exc:
        cdp["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    return cdp


@router.post("/settings/download-session/open-douyin")
async def open_douyin_download_session(body: DownloadSessionOpenRequest):
    """Launch/reuse Affiliate2's independent Douyin CDP browser session."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()

    try:
        browser = normalize_download_browser(
            body.douyin_browser if body.douyin_browser is not None
            else body.browser if body.browser is not None
            else getattr(row, "douyin_browser", None) or "edge"
        )
        cdp_url = normalize_douyin_cdp_url(
            body.douyin_cdp_url if body.douyin_cdp_url is not None
            else getattr(row, "douyin_cdp_url", None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from backend.services.download_browser_launcher import launch_douyin_browser_session
    selected_override = _validated_browser_override(
        browser,
        getattr(row, "douyin_executable_path", None),
    )
    launch = await launch_douyin_browser_session(
        cdp_url,
        browser=browser,
        executable_override=selected_override,
    )
    actual_cdp_url = launch.get("cdp_url") or cdp_url
    if launch.get("ok"):
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == "local")
            )
            target = result.scalar_one_or_none()
            if not target:
                target = UserSettings(user_id="local")
                db.add(target)
            target.douyin_browser = launch.get("resolved_browser") or browser
            target.douyin_cdp_url = actual_cdp_url
            if launch.get("executable"):
                target.douyin_executable_path = launch["executable"]
            await db.commit()

    launch.setdefault("authenticated", False)
    launch.setdefault("auth_required", False)
    if not launch.get("connected"):
        return launch

    cdp = await _probe_douyin_cdp(actual_cdp_url)
    probe_connected = bool(cdp.get("connected"))
    authenticated = bool(cdp.get("authenticated"))
    auth_required = bool(probe_connected and not authenticated)
    launch.update({
        "connected": bool(launch.get("connected") or probe_connected),
        "authenticated": authenticated,
        "auth_required": auth_required,
        "blocking_reason": None if authenticated else "douyin_login_required" if auth_required else "cdp_unreachable",
        "cdp_error": cdp.get("error"),
    })
    if authenticated:
        launch["message"] = "Douyin authenticated browser session is ready."
    elif auth_required:
        launch["message"] = "Browser opened. Log in to Douyin in that window, then click Check again."
    return launch


@router.post("/settings/download-session/health")
async def download_session_health(body: DownloadSessionHealthRequest):
    """Report Douyin CDP and yt-dlp cookie health as independent systems."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()

    try:
        cookie_browser = normalize_download_browser(
            body.cookie_browser if body.cookie_browser is not None
            else body.browser if body.browser is not None
            else getattr(row, "download_cookie_browser", None)
        )
        cookie_profile = normalize_download_profile(
            body.cookie_profile if body.cookie_profile is not None
            else body.profile if body.profile is not None
            else getattr(row, "download_cookie_profile", None)
        )
        douyin_browser = normalize_download_browser(
            body.douyin_browser if body.douyin_browser is not None
            else body.browser if body.browser is not None and getattr(row, "douyin_browser", None) is None
            else getattr(row, "douyin_browser", None) or "edge"
        )
        cdp_url = normalize_douyin_cdp_url(
            body.douyin_cdp_url if body.douyin_cdp_url is not None
            else getattr(row, "douyin_cdp_url", None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from backend.services.ytdlp_service import probe_browser_session
    from backend.services.download_browser_launcher import (
        describe_douyin_browser_runtime,
        resolve_browser_executable,
    )

    cookie = await probe_browser_session(cookie_browser, cookie_profile)
    cdp = await _probe_douyin_cdp(cdp_url)
    douyin_override = getattr(row, "douyin_executable_path", None)
    browser_runtime = (
        describe_douyin_browser_runtime(douyin_browser, cdp_url, douyin_override)
        if douyin_override
        else describe_douyin_browser_runtime(douyin_browser, cdp_url)
    )

    executable_found = bool(browser_runtime.get("executable_found", browser_runtime.get("executable")))
    authenticated = bool(cdp.get("authenticated"))
    if not executable_found:
        douyin_blocking = "browser_not_found"
    elif not cdp.get("endpoint_reachable"):
        douyin_blocking = "cdp_unreachable"
    elif not cdp.get("websocket_connected"):
        douyin_blocking = "cdp_websocket_unavailable"
    elif not cdp.get("page_reachable"):
        douyin_blocking = "douyin_page_unreachable"
    elif not authenticated:
        douyin_blocking = "douyin_login_required"
    else:
        douyin_blocking = None

    douyin_session = {
        "browser": douyin_browser,
        "executable_found": executable_found,
        "executable_path": browser_runtime.get("executable"),
        "detection_source": browser_runtime.get("detection_source"),
        "profile_path": browser_runtime.get("profile_dir"),
        "profile_ready": bool(browser_runtime.get("profile_ready")),
        "process_running": bool(cdp.get("endpoint_reachable")),
        "cdp_url": cdp_url,
        "cdp_http_reachable": bool(cdp.get("endpoint_reachable")),
        "cdp_websocket_connected": bool(cdp.get("websocket_connected")),
        "page_reachable": bool(cdp.get("page_reachable")),
        "authenticated": authenticated,
        "ready": authenticated,
        "blocking_reason": douyin_blocking,
        "error": cdp.get("error") or browser_runtime.get("executable_error"),
    }

    resolved_cookie_browser = cookie.get("resolved_browser")
    cookie_executable = (
        resolve_browser_executable(
            resolved_cookie_browser,
            getattr(row, "download_cookie_executable_path", None),
        )
        if resolved_cookie_browser
        else {"found": False, "executable_path": None, "detection_source": None}
    )
    cookie_source = {
        **cookie,
        "browser": resolved_cookie_browser or cookie_browser,
        "selected_browser": cookie_browser,
        "profile": cookie.get("profile") or cookie_profile,
        "browser_found": bool(cookie_executable.get("found")),
        "executable_path": cookie_executable.get("executable_path"),
        "detection_source": cookie_executable.get("detection_source"),
        "tiktok_cookie_present": bool(cookie.get("tiktok_cookie_present", int(cookie.get("tiktok_cookie_count") or 0) > 0)),
        "tiktok_ready": bool(cookie.get("tiktok_ready", cookie.get("ok") and int(cookie.get("tiktok_cookie_count") or 0) > 0)),
    }

    return {
        "douyin_session": douyin_session,
        "cookie_source": cookie_source,
        # Compatibility fields for existing clients and tests.
        "browser": cookie_browser,
        "profile": cookie_profile,
        "browser_runtime": browser_runtime,
        "cookie": cookie_source,
        "douyin_cdp": cdp,
        "douyin_ready": douyin_session["ready"],
        "tiktok_ready": cookie_source["tiktok_ready"],
    }


@router.post("/settings/download-session/open-tiktok")
async def open_tiktok_download_session(body: CookieBrowserOpenRequest):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        row = result.scalar_one_or_none()
    try:
        browser = normalize_download_browser(
            body.cookie_browser if body.cookie_browser is not None
            else getattr(row, "download_cookie_browser", None)
        )
        profile = normalize_download_profile(
            body.cookie_profile if body.cookie_profile is not None
            else getattr(row, "download_cookie_profile", None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if browser == "auto":
        from backend.services.ytdlp_service import probe_browser_session
        inspected = await probe_browser_session("auto", profile)
        if not inspected.get("ok"):
            return {"ok": False, "status": "cookie_source_unavailable", **inspected}
        browser = inspected["resolved_browser"]
        profile = inspected.get("profile")
    elif browser == "firefox" and not profile:
        from backend.services.download_browser_launcher import discover_firefox_profiles
        profiles = discover_firefox_profiles()
        default = next((p for p in profiles if p.get("is_default")), None) or profiles[:1]
        if default and default.get("path"):
            profile = default["path"]
        else:
            return {
                "ok": False,
                "browser": "firefox",
                "profile": None,
                "status": "profile_not_found",
                "message": "No readable Firefox profile found. Open Firefox to create one, or select a different browser.",
            }
    from backend.services.download_browser_launcher import open_cookie_source_browser
    selected_override = _validated_browser_override(
        browser,
        getattr(row, "download_cookie_executable_path", None),
    )
    return open_cookie_source_browser(
        browser,
        profile,
        executable_override=selected_override,
    )


@router.post("/settings/download-session/repair")
async def repair_download_session(body: DownloadSessionRepairRequest):
    scope = (body.scope or "all").strip().lower()
    if scope not in {"all", "douyin", "cookie"}:
        raise HTTPException(status_code=400, detail="Repair scope must be all, douyin, or cookie")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        row = result.scalar_one_or_none()

    douyin_repair = None
    cookie_repair = None
    if scope in {"all", "douyin"}:
        from backend.services.download_browser_launcher import (
            launch_douyin_browser_session,
            resolve_browser_executable,
        )
        douyin_browser = normalize_download_browser(getattr(row, "douyin_browser", None) or "edge")
        cdp_url = normalize_douyin_cdp_url(getattr(row, "douyin_cdp_url", None))
        override = getattr(row, "douyin_executable_path", None)
        if override:
            override_state = resolve_browser_executable(
                douyin_browser,
                override=override,
            )
            if not override_state.get("found"):
                override = None
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
                    persisted = result.scalar_one_or_none()
                    if persisted is not None:
                        persisted.douyin_executable_path = None
                        await db.commit()
        douyin_repair = await launch_douyin_browser_session(
            cdp_url,
            browser=douyin_browser,
            **({"executable_override": override} if override else {}),
        )

    if scope in {"all", "cookie"}:
        from backend.services.ytdlp_service import probe_browser_session
        saved_cookie_browser = normalize_download_browser(getattr(row, "download_cookie_browser", None) or "auto")
        saved_cookie_profile = normalize_download_profile(getattr(row, "download_cookie_profile", None))
        repair_browser = saved_cookie_browser if saved_cookie_browser != "auto" else "auto"
        cookie_repair = await probe_browser_session(repair_browser, saved_cookie_profile)
        if repair_browser != "auto" and cookie_repair.get("ok"):
            cookie_repair["resolved_browser"] = repair_browser
            cookie_repair["selected_browser"] = repair_browser

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        target = result.scalar_one_or_none()
        if not target:
            target = UserSettings(user_id="local")
            db.add(target)
        if douyin_repair and douyin_repair.get("ok"):
            target.douyin_browser = douyin_repair.get("resolved_browser") or "edge"
            target.douyin_cdp_url = douyin_repair.get("cdp_url") or normalize_douyin_cdp_url(None)
            if douyin_repair.get("executable"):
                target.douyin_executable_path = douyin_repair["executable"]
        if cookie_repair and cookie_repair.get("ok"):
            if saved_cookie_browser == "auto":
                target.download_cookie_browser = cookie_repair["resolved_browser"]
                target.download_cookie_profile = cookie_repair.get("profile")
            else:
                target.download_cookie_browser = saved_cookie_browser
                if cookie_repair.get("profile"):
                    target.download_cookie_profile = cookie_repair["profile"]
        await db.commit()

    return {
        "ok": bool(
            (douyin_repair is None or douyin_repair.get("ok"))
            and (cookie_repair is None or cookie_repair.get("ok"))
        ),
        "douyin_repair": douyin_repair,
        "cookie_repair": cookie_repair,
        "douyin_login_required": bool(douyin_repair and douyin_repair.get("connected") and not douyin_repair.get("authenticated")),
        "tiktok_login_required": bool(cookie_repair and cookie_repair.get("ok") and not cookie_repair.get("tiktok_ready")),
    }


@router.post("/settings/download-session/reset-douyin")
async def reset_douyin_download_session(body: DownloadSessionResetRequest):
    from backend.services.download_browser_launcher import reset_managed_douyin_session
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        row = result.scalar_one_or_none()
    browser = normalize_download_browser(getattr(row, "douyin_browser", None) or "edge")
    if browser == "auto":
        browser = "edge"
    return await asyncio.to_thread(
        reset_managed_douyin_session,
        confirm=body.confirm,
        clear_profile=body.clear_profile,
        browser=browser,
    )


@router.post("/settings/download-session/select-executable")
async def select_download_browser_executable(body: DownloadExecutablePickerRequest):
    target = (body.target or "").strip().lower()
    browser = normalize_download_browser(body.browser)
    if target not in {"douyin", "cookie"}:
        raise HTTPException(status_code=400, detail="Target must be douyin or cookie")
    expected = {
        "edge": "msedge.exe",
        "chrome": "chrome.exe",
        "brave": "brave.exe",
        "firefox": "firefox.exe",
    }.get(browser)
    if not expected:
        raise HTTPException(status_code=400, detail="Choose a concrete browser before selecting an executable")

    def choose_file() -> str:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            return filedialog.askopenfilename(
                title=f"Chọn {expected}",
                filetypes=[("Browser executable", expected), ("Executable", "*.exe")],
            )
        finally:
            root.destroy()

    try:
        selected_path = await asyncio.to_thread(choose_file)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Native file picker unavailable: {exc}") from exc
    if not selected_path:
        return {"ok": False, "status": "cancelled"}
    from backend.services.download_browser_launcher import resolve_browser_executable
    resolution = resolve_browser_executable(browser, selected_path)
    if not resolution["found"]:
        raise HTTPException(status_code=400, detail=resolution["error"])

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == "local"))
        row = result.scalar_one_or_none()
        if not row:
            row = UserSettings(user_id="local")
            db.add(row)
        if target == "douyin":
            if browser not in {"edge", "chrome", "brave"}:
                raise HTTPException(status_code=400, detail="Douyin requires Edge, Chrome, or Brave")
            row.douyin_browser = browser
            row.douyin_executable_path = selected_path
        else:
            row.download_cookie_browser = browser
            row.download_cookie_executable_path = selected_path
        await db.commit()
    return {"ok": True, "target": target, **resolution}


@router.post("/settings/download-session/test")
async def test_download_session(body: DownloadSessionProbeRequest):
    """Verify that yt-dlp can read the selected local browser/profile."""
    if body.browser is None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == "local")
            )
            row = result.scalar_one_or_none()
        browser = normalize_download_browser(
            getattr(row, "download_cookie_browser", None)
        )
        profile = normalize_download_profile(
            getattr(row, "download_cookie_profile", None)
        )
    else:
        try:
            browser = normalize_download_browser(body.browser)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        profile = normalize_download_profile(body.profile)

    from backend.services.ytdlp_service import probe_browser_session
    return await probe_browser_session(browser, profile)


@router.get("/settings/health")
async def get_health():
    """Return system health status."""
    import shutil
    import subprocess

    health = {}

    # Canonical downloader browser-session health. The remaining legacy
    # TikTok upload-cookie fallback is upload-only and does not define
    # discovery/downloader readiness.
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = res.scalar_one_or_none()

    from backend.services.ytdlp_service import browser_session_health
    health["download_session"] = browser_session_health(
        normalize_download_browser(getattr(s, "download_cookie_browser", None)),
        normalize_download_profile(getattr(s, "download_cookie_profile", None)),
    )

    # ImageMagick
    if shutil.which("magick") or shutil.which("convert"):
        health["imagemagick"] = {"status": "ok"}
    else:
        health["imagemagick"] = {"status": "not_found"}

    # yt-dlp
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=5)
        health["ytdlp"] = {"status": "ok", "version": r.stdout.strip()}
    except Exception:
        health["ytdlp"] = {"status": "not_found"}

    # AI provider configured (BYOK)
    has_ai_key = bool(
        app_settings.ANTHROPIC_API_KEY
        or app_settings.OPENAI_API_KEY
        or (s and s.ai_api_key_encrypted)
    )
    health["ai_provider"] = {"status": "configured" if has_ai_key else "not_configured"}

    # YouTube API key (BYOK — per-user encrypted or .env fallback)
    has_youtube_key = bool(
        app_settings.YOUTUBE_API_KEY
        or (s and s.youtube_api_key_encrypted)
    )
    health["youtube_api_key"] = {"status": "configured" if has_youtube_key else "not_configured"}

    # YouTube quota
    from backend.services.youtube_quota import _usage_today, DAILY_LIMIT
    health["youtube_quota"] = {"used": _usage_today.get("count", 0), "limit": DAILY_LIMIT}

    return health


@router.post("/settings/open-folder")
async def open_folder(body: dict):
    """Open a storage folder in the OS file manager."""
    import platform, subprocess
    from pathlib import Path

    folder_map = {
        "videos": app_settings.VIDEOS_DIR,
        "audio": app_settings.AUDIO_DIR,
        "generated": app_settings.GENERATED_DIR,
        "thumbnails": app_settings.THUMBNAILS_DIR,
        "storage": app_settings.STORAGE_ROOT,
    }

    folder_key = body.get("folder", "storage")
    target = folder_map.get(folder_key, app_settings.STORAGE_ROOT)
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(target)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return {"ok": True, "path": str(target)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": str(target)}


# ── Motion Graphics (HyperFrames) — on-demand Node plugin ─────────────────────
# Nothing motion-related ships in the base install. The portable Node runtime
# and the HyperFrames npm package are downloaded on opt-in and live entirely in
# the user's data dir, so the feature can be added and removed without touching
# the app. Rendering is local (headless Chrome + FFmpeg) and costs nothing.

@router.get("/settings/motion-graphics/status")
async def motion_graphics_status():
    """Plugin install state for the Settings card to poll (every 2s)."""
    from backend.services.hyperframes_service import HyperFramesService
    return HyperFramesService.get_install_state()


@router.post("/settings/motion-graphics/install")
async def motion_graphics_install():
    """Trigger the plugin install (portable Node + npm install) in the
    background. Idempotent; returns immediately. The UI polls status for
    progress."""
    from backend.services.hyperframes_service import HyperFramesService
    return await HyperFramesService.install_plugin()


@router.post("/settings/motion-graphics/uninstall")
async def motion_graphics_uninstall():
    """Remove the entire motion/ plugin dir."""
    from backend.services.hyperframes_service import HyperFramesService
    return await HyperFramesService.uninstall_plugin()


@router.post("/settings/motion-graphics/test")
async def motion_graphics_test():
    """End-to-end smoke render of the bundled kinetic-hook template.

    Confirms the whole chain (portable Node → HyperFrames CLI → headless Chrome
    → FFmpeg) still produces a real MP4. The status is always 200 — the `ok`
    flag carries the verdict, so the UI renders a result rather than an error.
    """
    import time as _time
    from backend.services.hyperframes_service import HyperFramesService
    from backend.services.motion_render_service import MotionRenderService

    if not HyperFramesService.is_installed():
        return {
            "ok": False, "latency_ms": 0, "output_size_kb": None,
            "error": "not_installed",
            "detail": "Motion Graphics isn't installed yet. Click Install above first.",
        }

    start = _time.monotonic()
    try:
        out = await MotionRenderService.render(
            template_id="kinetic_hook",
            variables={
                "kicker": "MOTION GRAPHICS",
                "title": "It Works",
                "subtitle": "rendered locally",
                "accent": "#ffd60a",
                "bg": "#0b0b14",
            },
            aspect="9:16",
            # A verification render, not a deliverable: draft quality and no
            # supersample keep it fast.
            quality="draft",
            supersample=False,
        )
        latency_ms = round((_time.monotonic() - start) * 1000)
        size_kb = round(out.stat().st_size / 1024)
        # Smoke test only — don't keep the output around.
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "ok": True, "latency_ms": latency_ms,
            "output_size_kb": size_kb, "error": None, "detail": None,
        }
    except Exception as e:
        latency_ms = round((_time.monotonic() - start) * 1000)
        logger.info(
            "Motion Graphics smoke test failed in %dms: %s: %s",
            latency_ms, type(e).__name__, str(e)[:200],
        )
        return {
            "ok": False, "latency_ms": latency_ms, "output_size_kb": None,
            "error": type(e).__name__, "detail": str(e)[:200],
        }
