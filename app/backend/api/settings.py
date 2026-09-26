# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/settings — user settings CRUD + credential testing + OAuth callbacks."""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
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
    meta_page_access_token_set: bool = False

    # OAuth / browser-session status
    download_cookie_browser: str = "auto"
    download_cookie_profile: Optional[str] = None
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
    meta_page_access_token: Optional[str] = None
    meta_page_id: Optional[str] = None

    # Canonical yt-dlp browser session. Cookies are never copied into this
    # request/model; only the local browser/profile selector is persisted.
    download_cookie_browser: Optional[str] = None
    download_cookie_profile: Optional[str] = None
    douyin_cdp_url: Optional[str] = None

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
        meta_page_access_token_set=bool(getattr(s, "meta_page_access_token_encrypted", None)),
        download_cookie_browser=normalize_download_browser(s.download_cookie_browser),
        download_cookie_profile=normalize_download_profile(s.download_cookie_profile),
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
        if update.douyin_cdp_url is not None:
            try:
                s.douyin_cdp_url = normalize_douyin_cdp_url(update.douyin_cdp_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    browser: Optional[str] = None
    profile: Optional[str] = None
    douyin_cdp_url: Optional[str] = None


@router.post("/settings/download-session/health")
async def download_session_health(body: DownloadSessionHealthRequest):
    """Report cookie readability and authenticated Douyin CDP readiness separately."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        row = result.scalar_one_or_none()

    try:
        browser = normalize_download_browser(
            body.browser if body.browser is not None
            else getattr(row, "download_cookie_browser", None)
        )
        profile = normalize_download_profile(
            body.profile if body.browser is not None
            else getattr(row, "download_cookie_profile", None)
        )
        cdp_url = normalize_douyin_cdp_url(
            body.douyin_cdp_url if body.douyin_cdp_url is not None
            else getattr(row, "douyin_cdp_url", None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from backend.services.ytdlp_service import probe_browser_session
    cookie = await probe_browser_session(browser, profile)

    cdp = {
        "url": cdp_url,
        "connected": False,
        "authenticated": False,
        "error": None,
    }
    try:
        from backend.services.douyin_browser_session import open_douyin_cdp_session
        async with open_douyin_cdp_session(
            cdp_url,
            connect_timeout_ms=4_000,
            navigation_timeout_ms=8_000,
        ) as session:
            cdp.update({
                "connected": True,
                "authenticated": True,
                "cookie_count": len(session.cookie_dict),
                "has_ms_token": bool(session.ms_token),
                "user_agent": session.user_agent[:160] if session.user_agent else "",
            })
    except PermissionError as exc:
        message = str(exc)
        cdp["connected"] = "Cannot connect to Douyin CDP" not in message
        cdp["error"] = message[:500]
    except Exception as exc:
        cdp["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"

    return {
        "browser": browser,
        "profile": profile,
        "cookie": cookie,
        "douyin_cdp": cdp,
        "douyin_ready": bool(
            (cookie.get("ok") and int(cookie.get("douyin_cookie_count") or 0) > 0)
            or cdp["authenticated"]
        ),
        "tiktok_ready": bool(
            cookie.get("ok") and int(cookie.get("tiktok_cookie_count") or 0) > 0
        ),
    }


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
