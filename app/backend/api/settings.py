# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/settings — user settings CRUD + credential testing + OAuth callbacks."""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional

from backend.core.localization import LocalizationProfile
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.user_settings import UserSettings
from backend.core.crypto import encrypt, decrypt_safe
from backend.config import settings as app_settings
from backend.services.download_browser_session import (
    SUPPORTED_DOWNLOAD_BROWSERS,
    normalize_download_browser,
    normalize_download_profile,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SettingsResponse(BaseModel):
    # AI provider preference (BYOK)
    ai_provider: str = "openai"
    ai_model: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_api_key_set: bool = False  # masked — never returns the actual key

    # Service API keys (BYOK) — booleans only, never the plaintext key
    youtube_api_key_set: bool = False
    huggingface_token_set: bool = False

    # OAuth / browser-session status
    download_cookie_browser: str = "auto"
    download_cookie_profile: Optional[str] = None
    youtube_channel_title: Optional[str] = None
    youtube_connected: bool = False
    tiktok_upload_connected: bool = False

    # Preferences
    preferred_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    tts_provider: str = "edge_tts"
    preferred_tts_voice: Optional[str] = None
    tts_model: Optional[str] = None
    tts_base_url: Optional[str] = None
    tts_region: Optional[str] = None
    tts_api_key_set: bool = False
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

    # Service API keys (BYOK) — plaintext on input, encrypted before storage.
    # Send empty string to clear a stored key.
    youtube_api_key: Optional[str] = None
    huggingface_token: Optional[str] = None

    # Canonical yt-dlp browser session. Cookies are never copied into this
    # request/model; only the local browser/profile selector is persisted.
    download_cookie_browser: Optional[str] = None
    download_cookie_profile: Optional[str] = None

    # Preferences
    preferred_voice_id: Optional[str] = None
    tts_provider: Optional[str] = None
    preferred_tts_voice: Optional[str] = None
    tts_model: Optional[str] = None
    tts_base_url: Optional[str] = None
    tts_region: Optional[str] = None
    tts_api_key: Optional[str] = None
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
        youtube_api_key_set=bool(s.youtube_api_key_encrypted),
        huggingface_token_set=bool(s.huggingface_token_encrypted),
        download_cookie_browser=normalize_download_browser(s.download_cookie_browser),
        download_cookie_profile=normalize_download_profile(s.download_cookie_profile),
        youtube_channel_title=s.youtube_channel_title,
        youtube_connected=bool(s.youtube_credentials_json_encrypted),
        tiktok_upload_connected=bool(s.tiktok_upload_token_encrypted),
        preferred_voice_id=s.preferred_voice_id or "21m00Tcm4TlvDq8ikWAM",
        tts_provider=s.tts_provider or "edge_tts",
        preferred_tts_voice=s.preferred_tts_voice,
        tts_model=s.tts_model,
        tts_base_url=s.tts_base_url,
        tts_region=s.tts_region,
        tts_api_key_set=bool(s.tts_api_key_encrypted),
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

        # AI provider preference (BYOK). One encrypted key is stored, so a
        # provider switch without a replacement key MUST clear the old secret;
        # otherwise an OpenAI key can be sent to Anthropic/a local endpoint.
        if update.ai_provider is not None:
            new_provider = update.ai_provider.strip().lower()
            if new_provider != (s.ai_provider or "").strip().lower() and update.ai_api_key is None:
                s.ai_api_key_encrypted = None
            s.ai_provider = new_provider
        if update.ai_model is not None:
            s.ai_model = update.ai_model or None
        if update.ai_base_url is not None:
            s.ai_base_url = update.ai_base_url.strip() or None
        if update.ai_api_key is not None:
            # Empty string clears the stored key; otherwise encrypt + store
            s.ai_api_key_encrypted = encrypt(update.ai_api_key) if update.ai_api_key else None

        # Service API keys (BYOK) — empty string clears, populated value encrypts
        if update.youtube_api_key is not None:
            s.youtube_api_key_encrypted = encrypt(update.youtube_api_key) if update.youtube_api_key else None
        if update.huggingface_token is not None:
            s.huggingface_token_encrypted = encrypt(update.huggingface_token) if update.huggingface_token else None

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

        # Preference fields
        if update.preferred_voice_id is not None:
            s.preferred_voice_id = update.preferred_voice_id
        if update.tts_provider is not None:
            new_tts_provider = update.tts_provider.strip().lower()
            provider_changed = (
                new_tts_provider != (s.tts_provider or "").strip().lower()
            )
            if provider_changed:
                if update.tts_api_key is None:
                    s.tts_api_key_encrypted = None
                # Provider-scoped identifiers/endpoints must not silently ride
                # across a switch (e.g. an Azure voice/base URL into ElevenLabs).
                if update.preferred_tts_voice is None:
                    s.preferred_tts_voice = None
                if update.tts_model is None:
                    s.tts_model = None
                if update.tts_base_url is None:
                    s.tts_base_url = None
                if update.tts_region is None:
                    s.tts_region = None
            s.tts_provider = new_tts_provider
        if update.preferred_tts_voice is not None:
            s.preferred_tts_voice = update.preferred_tts_voice or None
        if update.tts_model is not None:
            s.tts_model = update.tts_model.strip() or None
        if update.tts_base_url is not None:
            s.tts_base_url = update.tts_base_url.strip() or None
        if update.tts_region is not None:
            s.tts_region = update.tts_region.strip() or None
        if update.tts_api_key is not None:
            s.tts_api_key_encrypted = encrypt(update.tts_api_key) if update.tts_api_key else None
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


# ── OAuth endpoints ──────────────────────────────────────────────────────────


class AIProbeRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


async def _probe_key(body: AIProbeRequest) -> str:
    if body.api_key is not None:
        return body.api_key.strip()

    provider = (body.provider or "").lower().strip()
    if provider in {"ollama", "lmstudio", "vllm"}:
        return ""

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
    # A stored key only belongs to the stored provider. Never reuse it while
    # probing a different endpoint/provider selected in the edit form.
    if not s or (s.ai_provider or "").lower().strip() != provider:
        return ""
    return decrypt_safe(s.ai_api_key_encrypted) if s.ai_api_key_encrypted else ""


@router.post("/settings/ai/models")
async def discover_ai_models(body: AIProbeRequest):
    """Discover models from OpenAI/OpenRouter/OpenAI-compatible endpoints."""
    from backend.core.ai_provider import (
        COMPATIBLE_PRESETS, compatible_base_url, list_openai_compatible_models,
    )

    provider = (body.provider or "").lower().strip()
    if provider == "openai":
        base_url = "https://api.openai.com/v1"
    elif provider == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
    elif provider in COMPATIBLE_PRESETS or provider == "compatible":
        base_url = compatible_base_url(provider, body.base_url)
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

    key = await _probe_key(body)
    try:
        client = make_ai_client(
            provider_name=body.provider,
            api_key=key,
            model=body.model,
            base_url=body.base_url,
        )
        reply = await client.chat(
            messages=[{"role": "user", "content": "Reply with exactly OK."}],
            max_tokens=8,
        )
        return {"ok": True, "response": (reply or "").strip()[:80]}
    except Exception as exc:
        logger.warning("AI connection test failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(exc)[:200]}")


@router.get("/settings/localization-profiles")
async def get_localization_profiles():
    from backend.core.localization import load_profile_store

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
    store = load_profile_store(s.localization_profiles_json if s else None)
    return store.model_dump()


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
        try:
            store = upsert_profile(store, profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        s.localization_profiles_json = store.model_dump_json()
        await db.commit()
    return store.model_dump()


@router.delete("/settings/localization-profiles/{profile_id}")
async def remove_localization_profile(profile_id: str):
    from backend.core.localization import delete_profile, load_profile_store

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        s = result.scalar_one_or_none()
        if not s:
            return load_profile_store(None).model_dump()
        store = delete_profile(load_profile_store(s.localization_profiles_json), profile_id)
        s.localization_profiles_json = store.model_dump_json()
        await db.commit()
    return store.model_dump()


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
    return store.model_dump()


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


class DownloadSessionProbeRequest(BaseModel):
    browser: Optional[str] = None
    profile: Optional[str] = None


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
