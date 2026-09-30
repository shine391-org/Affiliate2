# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.api import settings as settings_api
from backend.core.localization import LocalizationProfile
from backend.services import elevenlabs_tts_service as eleven
from backend.services import localization_service as localization
from backend.services import tts_service


class _Response:
    def __init__(self, status_code=200, payload=None, text="", content=b""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = content

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses, seen):
        self._responses = list(responses)
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self._seen.append(("GET", url, dict(headers or {})))
        return self._responses.pop(0)

    async def post(self, url, headers=None, json=None):
        self._seen.append(("POST", url, dict(headers or {}), json))
        return self._responses.pop(0)


def test_elevenlabs_official_base_url_adds_v1_once():
    assert eleven.canonicalize_base_url("https://api.elevenlabs.io") == "https://api.elevenlabs.io/v1"
    assert eleven.canonicalize_base_url("https://api.elevenlabs.io/") == "https://api.elevenlabs.io/v1"
    assert eleven.build_api_url("https://api.elevenlabs.io", "voices") == "https://api.elevenlabs.io/v1/voices"


def test_elevenlabs_canonical_base_url_never_duplicates_v1():
    assert eleven.canonicalize_base_url("https://api.elevenlabs.io/v1") == "https://api.elevenlabs.io/v1"
    assert eleven.canonicalize_base_url("https://api.elevenlabs.io/v1/") == "https://api.elevenlabs.io/v1"
    assert eleven.canonicalize_base_url("https://api.elevenlabs.io/v1/v1") == "https://api.elevenlabs.io/v1"
    assert "/v1/v1/" not in eleven.build_api_url("https://api.elevenlabs.io/v1", "text-to-speech/voice-1")


def test_elevenlabs_custom_endpoint_is_not_rewritten():
    assert eleven.canonicalize_base_url("https://tts.example.test/proxy") == "https://tts.example.test/proxy"


@pytest.mark.asyncio
async def test_elevenlabs_invalid_key_is_classified_as_auth(monkeypatch):
    seen = []
    monkeypatch.setattr(
        eleven.httpx,
        "AsyncClient",
        lambda **kwargs: _Client([_Response(401, {"detail": "invalid key"}, text="invalid key")], seen),
    )

    with pytest.raises(eleven.ElevenLabsAPIError) as exc:
        await eleven.list_voices(api_key="bad-secret")

    assert exc.value.kind == "auth"
    assert exc.value.status_code == 401
    assert "bad-secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_elevenlabs_voice_discovery_returns_ids_names_and_labels(monkeypatch):
    seen = []
    monkeypatch.setattr(
        eleven.httpx,
        "AsyncClient",
        lambda **kwargs: _Client([
            _Response(200, {"voices": [{
                "voice_id": "voice-1",
                "name": "Rachel",
                "category": "premade",
                "labels": {"accent": "american"},
            }]})
        ], seen),
    )

    voices = await eleven.list_voices(api_key="secret")

    assert voices == [{
        "voice_id": "voice-1",
        "name": "Rachel",
        "category": "premade",
        "labels": {"accent": "american"},
    }]
    assert seen[0][1] == "https://api.elevenlabs.io/v1/voices"


@pytest.mark.asyncio
async def test_tts_health_detects_missing_selected_elevenlabs_voice(monkeypatch):
    async def fake_voices(**kwargs):
        return [{"voice_id": "available", "name": "Available", "category": "premade", "labels": {}}]

    async def fake_models(**kwargs):
        return [{"model_id": "eleven_multilingual_v2", "name": "Multilingual v2"}]

    monkeypatch.setattr(eleven, "list_voices", fake_voices)
    monkeypatch.setattr(eleven, "list_models", fake_models)

    result = await tts_service.test_tts_connection(
        provider=tts_service.TTSProvider.ELEVENLABS,
        api_key="secret",
        voice_id="missing",
        model="eleven_multilingual_v2",
    )

    assert result["ok"] is False
    assert result["selected_voice_valid"] is False
    assert result["model_valid"] is True
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_elevenlabs_voice_discovery_also_returns_selectable_models(monkeypatch):
    async def fake_voices(provider, key, *, base_url=None):
        assert provider == tts_service.TTSProvider.ELEVENLABS
        assert key == "secret"
        return [{"voice_id": "voice-1", "name": "Voice 1"}]

    async def fake_models(*, api_key, base_url=None):
        assert api_key == "secret"
        return [{"model_id": "eleven_multilingual_v2", "name": "Multilingual v2"}]

    async def no_db_lookup():
        raise AssertionError("unsaved TTS discovery must not read UserSettings")

    monkeypatch.setattr(settings_api, "_load_saved_settings", no_db_lookup)
    monkeypatch.setattr(settings_api, "_tts_probe_key", lambda body: _async_value("secret"))
    monkeypatch.setattr(tts_service, "list_voices", fake_voices)
    monkeypatch.setattr(eleven, "list_models", fake_models)

    result = await settings_api.discover_tts_voices(
        settings_api.TTSProbeRequest(provider="elevenlabs")
    )

    assert result["voices"][0]["voice_id"] == "voice-1"
    assert result["models"][0]["model_id"] == "eleven_multilingual_v2"


@pytest.mark.asyncio
async def test_gpt_sovits_never_reports_a_verified_connection():
    result = await tts_service.test_tts_connection(
        provider=tts_service.TTSProvider.GPT_SOVITS,
        voice_id="",
        base_url="http://127.0.0.1:9880",
    )

    assert result["configuration_valid"] is True
    assert result["connection_verified"] is False
    assert "Cấu hình hợp lệ" in result["message"]
    assert "READY" not in result["message"]


@pytest.mark.asyncio
async def test_openai_tts_never_reports_a_verified_connection():
    result = await tts_service.test_tts_connection(
        provider=tts_service.TTSProvider.OPENAI_TTS,
        api_key="sk-not-a-real-key",
        voice_id="alloy",
        model="tts-1",
    )

    assert result["configuration_valid"] is True
    assert result["connection_verified"] is False


@pytest.mark.asyncio
async def test_probed_providers_report_a_verified_connection(monkeypatch):
    async def fake_edge_voices(*args, **kwargs):
        return [{"voice_id": "vi-VN-HoaiMyNeural", "name": "Mai"}]

    monkeypatch.setattr(
        tts_service, "list_voices", fake_edge_voices, raising=True,
    )

    result = await tts_service.test_tts_connection(
        provider=tts_service.TTSProvider.EDGE_TTS,
        voice_id="vi-VN-HoaiMyNeural",
    )

    assert result["connection_verified"] is True
    assert result["configuration_valid"] is True


@pytest.mark.asyncio
async def test_localization_preflight_rejects_unsupported_gpt_sovits_language(monkeypatch):
    """GPT-SoVITS must fail before separation/ASR for unsupported Vietnamese."""
    from backend.services import localization_service as localization

    runtime = localization.TTSRuntimeConfig(
        provider=tts_service.TTSProvider.GPT_SOVITS,
        default_voice="",
        api_key="",
        model=None,
        base_url="http://127.0.0.1:9880",
        region=None,
    )
    monkeypatch.setattr(localization, "_tts_config", lambda *_args, **_kwargs: runtime)

    with pytest.raises(ValueError, match="Chinese/English only"):
        await localization.validate_localization_tts_preflight(
            object(), SimpleNamespace(), "Vietnamese",
        )


@pytest.mark.asyncio
async def test_localization_preflight_accepts_supported_gpt_sovits_language(monkeypatch):
    from backend.services import localization_service as localization

    runtime = localization.TTSRuntimeConfig(
        provider=tts_service.TTSProvider.GPT_SOVITS,
        default_voice="",
        api_key="",
        model=None,
        base_url="http://127.0.0.1:9880",
        region=None,
    )
    monkeypatch.setattr(localization, "_tts_config", lambda *_args, **_kwargs: runtime)

    health = await localization.validate_localization_tts_preflight(
        object(), SimpleNamespace(), "English",
    )

    assert health["ok"] is True
    assert health["connection_verified"] is False


@pytest.mark.asyncio
async def test_settings_tts_test_uses_unsaved_plaintext_key(monkeypatch):
    seen = {}

    async def fake_test(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "provider": "elevenlabs", "authenticated": True, "message": "ready"}

    async def no_db_lookup():
        raise AssertionError("unsaved TTS test must not read UserSettings")

    monkeypatch.setattr(settings_api, "_load_saved_settings", no_db_lookup)
    monkeypatch.setattr(tts_service, "test_tts_connection", fake_test)
    body = settings_api.TTSProbeRequest(
        provider="elevenlabs",
        api_key="unsaved-secret",
        model="eleven_multilingual_v2",
        voice_id="voice-1",
    )

    result = await settings_api.test_tts_connection(body)

    assert seen["api_key"] == "unsaved-secret"
    assert "unsaved-secret" not in json.dumps(result)



@pytest.mark.asyncio
async def test_settings_tts_probe_restores_key_for_saved_nonactive_provider(monkeypatch):
    saved = SimpleNamespace(
        tts_provider="edge_tts",
        tts_api_key_encrypted=None,
        tts_provider_profiles_json="{}",
        tts_connections_json=json.dumps({
            "eleven-main": {
                "label": "ElevenLabs main",
                "provider": "elevenlabs",
                "voice": "voice-1",
                "model": "eleven_multilingual_v2",
                "base_url": "https://api.elevenlabs.io/v1",
                "region": None,
                "api_key_encrypted": "encrypted-eleven",
                "archived": False,
            },
        }),
    )
    monkeypatch.setattr(settings_api, "_load_saved_tts_settings", lambda: _async_value(saved))
    monkeypatch.setattr(settings_api, "decrypt_safe", lambda value: {
        "encrypted-eleven": "stored-eleven-secret",
    }[value])

    restored = await settings_api._tts_probe_key(
        settings_api.TTSProbeRequest(
            connection_id="eleven-main",
            provider="elevenlabs",
        )
    )
    unsaved = await settings_api._tts_probe_key(
        settings_api.TTSProbeRequest(provider="elevenlabs")
    )

    assert restored == "stored-eleven-secret"
    assert unsaved == ""

@pytest.mark.asyncio
async def test_openai_tts_probe_uses_saved_openai_ai_key_when_tts_slot_has_none(monkeypatch):
    saved = SimpleNamespace(
        tts_provider="edge_tts",
        tts_api_key_encrypted=None,
        tts_provider_profiles_json="{}",
        tts_connections_json=json.dumps({
            "openai-tts-main": {
                "label": "OpenAI TTS main",
                "provider": "openai_tts",
                "voice": "alloy",
                "model": "tts-1",
                "base_url": None,
                "region": None,
                "api_key_encrypted": None,
                "archived": False,
            },
        }),
        ai_provider="openai",
        ai_api_key_encrypted="enc-openai",
        ai_provider_profiles_json="{}",
    )
    monkeypatch.setattr(settings_api, "_load_saved_tts_settings", lambda: _async_value(saved))
    monkeypatch.setattr(settings_api, "decrypt_safe", lambda value: "openai-ai-secret" if value == "enc-openai" else "")

    key = await settings_api._tts_probe_key(
        settings_api.TTSProbeRequest(
            connection_id="openai-tts-main",
            provider="openai_tts",
        )
    )

    assert key == "openai-ai-secret"

@pytest.mark.asyncio
async def test_openai_tts_probe_uses_inactive_saved_openai_registry_key(monkeypatch):
    saved = SimpleNamespace(
        tts_provider="edge_tts",
        tts_api_key_encrypted=None,
        tts_provider_profiles_json="{}",
        tts_connections_json=json.dumps({
            "openai-tts-main": {
                "label": "OpenAI TTS main",
                "provider": "openai_tts",
                "voice": "alloy",
                "model": "tts-1",
                "base_url": None,
                "region": None,
                "api_key_encrypted": None,
                "archived": False,
            },
        }),
        ai_provider="compatible",
        ai_api_key_encrypted="enc-compatible",
        ai_provider_profiles_json=json.dumps({
            "openai": {"api_key_encrypted": "enc-openai-registry"},
        }),
    )
    monkeypatch.setattr(settings_api, "_load_saved_tts_settings", lambda: _async_value(saved))
    monkeypatch.setattr(settings_api, "decrypt_safe", lambda value: {
        "enc-openai-registry": "registry-openai-secret",
        "enc-compatible": "compatible-secret",
    }.get(value, ""))

    key = await settings_api._tts_probe_key(
        settings_api.TTSProbeRequest(
            connection_id="openai-tts-main",
            provider="openai_tts",
        )
    )

    assert key == "registry-openai-secret"
@pytest.mark.asyncio
async def test_localization_tts_preflight_rejects_invalid_voice_before_pipeline(monkeypatch):
    from backend.services import localization_service as localization

    runtime = localization.TTSRuntimeConfig(
        provider=tts_service.TTSProvider.ELEVENLABS,
        default_voice="missing",
        api_key="secret",
        model="eleven_multilingual_v2",
        base_url="https://api.elevenlabs.io/v1",
        region=None,
    )
    monkeypatch.setattr(localization, "_tts_config", lambda *_args, **_kwargs: runtime)

    async def fake_health(**kwargs):
        return {
            "ok": False,
            "selected_voice_valid": False,
            "model_valid": True,
            "message": "ElevenLabs voice 'missing' is missing or inaccessible.",
        }

    monkeypatch.setattr(tts_service, "test_tts_connection", fake_health)

    with pytest.raises(ValueError, match="missing or inaccessible"):
        await localization.validate_localization_tts_preflight(
            object(), SimpleNamespace(), "Vietnamese",
        )


@pytest.mark.asyncio
async def test_tts_preview_uses_exact_unsaved_form_values(monkeypatch, tmp_path):
    from backend.api import tts_preview

    captured = {}

    async def fake_key(body):
        assert body.api_key == "unsaved-secret"
        return body.api_key

    async def fake_generate(text, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        kwargs["output_path"].write_bytes(b"audio")
        return kwargs["output_path"]

    monkeypatch.setattr(settings_api, "_tts_probe_key", fake_key)
    monkeypatch.setattr(tts_service, "generate_tts", fake_generate)
    monkeypatch.setattr(type(tts_preview.settings), "TMP_DIR", property(lambda _self: tmp_path))

    response = await tts_preview.tts_preview(tts_preview._PreviewBody(
        provider="elevenlabs",
        api_key="unsaved-secret",
        base_url="https://api.elevenlabs.io",
        model="eleven_multilingual_v2",
        voice_id="voice-1",
        region=None,
    ))

    assert captured["text"] == "Xin chào, đây là bản thử giọng."
    assert captured["api_key"] == "unsaved-secret"
    assert captured["base_url"] == "https://api.elevenlabs.io"
    assert captured["model"] == "eleven_multilingual_v2"
    assert captured["voice_id"] == "voice-1"
    assert response.headers["cache-control"] == "no-store"
    assert "unsaved-secret" not in str(response.headers)


@pytest.mark.asyncio
async def test_tts_preview_uses_wav_container_for_wav_provider(monkeypatch, tmp_path):
    from backend.api import tts_preview

    async def fake_key(body):
        return "stored-key"

    async def fake_generate(text, **kwargs):
        assert kwargs["output_path"].suffix == ".wav"
        kwargs["output_path"].write_bytes(b"RIFF-test")
        return kwargs["output_path"]

    monkeypatch.setattr(settings_api, "_tts_probe_key", fake_key)
    monkeypatch.setattr(tts_service, "generate_tts", fake_generate)
    monkeypatch.setattr(type(tts_preview.settings), "TMP_DIR", property(lambda _self: tmp_path))

    response = await tts_preview.tts_preview(tts_preview._PreviewBody(
        provider="azure_tts",
        voice_id="vi-VN-HoaiMyNeural",
        region="southeastasia",
    ))

    assert response.media_type == "audio/wav"


@pytest.mark.asyncio
async def test_tts_preview_removes_partial_artifact_on_failure(monkeypatch, tmp_path):
    from backend.api import tts_preview
    from backend.core.exceptions import VoiceGenerationError

    async def fake_key(body):
        return "stored-key"

    async def fake_generate(text, **kwargs):
        kwargs["output_path"].write_bytes(b"partial")
        raise VoiceGenerationError("provider failed")

    monkeypatch.setattr(settings_api, "_tts_probe_key", fake_key)
    monkeypatch.setattr(tts_service, "generate_tts", fake_generate)
    monkeypatch.setattr(type(tts_preview.settings), "TMP_DIR", property(lambda _self: tmp_path))

    with pytest.raises(Exception):
        await tts_preview.tts_preview(tts_preview._PreviewBody(
            provider="edge_tts",
            voice_id="vi-VN-HoaiMyNeural",
        ))

    assert list((tmp_path / "tts_previews").glob("*")) == []


async def _async_value(value):
    return value


def test_profile_edge_tts_override_isolated_from_global_elevenlabs():
    user_settings = SimpleNamespace(
        tts_provider="elevenlabs",
        preferred_tts_voice="eleven-voice",
        tts_model="eleven_multilingual_v2",
        tts_base_url="https://api.elevenlabs.io/v1",
        tts_region="global-region",
        tts_api_key_encrypted="encrypted-eleven-key",
    )
    profile = LocalizationProfile(
        id="fast-edge",
        name="FAST Edge",
        tts_provider="edge_tts",
    )

    runtime = localization._tts_config(user_settings, profile, "Vietnamese")

    assert runtime.provider == tts_service.TTSProvider.EDGE_TTS
    assert runtime.default_voice == "vi-VN-HoaiMyNeural"
    assert runtime.api_key == ""
    assert runtime.model is None
    assert runtime.base_url is None
    assert runtime.region is None


def test_profile_credential_provider_cannot_reinterpret_another_tts_slot():
    user_settings = SimpleNamespace(
        tts_provider="elevenlabs",
        preferred_tts_voice="eleven-voice",
        tts_model="eleven_multilingual_v2",
        tts_base_url="https://api.elevenlabs.io/v1",
        tts_region=None,
        tts_api_key_encrypted="encrypted-eleven-key",
    )
    profile = LocalizationProfile(
        id="bad-openai",
        name="Bad OpenAI override",
        tts_provider="openai_tts",
    )

    with pytest.raises(ValueError, match="no saved TTS connection"):
        localization._tts_config(user_settings, profile, "Vietnamese")


def test_provider_registry_public_views_never_expose_encrypted_keys():
    row = SimpleNamespace(
        ai_provider="openai",
        ai_model="gpt-current",
        ai_base_url=None,
        ai_api_key_encrypted="enc-openai",
        ai_provider_profiles_json=json.dumps({
            "openrouter": {
                "model": "anthropic/claude-sonnet-4.6",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_encrypted": "enc-openrouter",
            },
        }),
        tts_provider="edge_tts",
        preferred_tts_voice="vi-VN-HoaiMyNeural",
        tts_model=None,
        tts_base_url=None,
        tts_region=None,
        tts_api_key_encrypted=None,
        tts_provider_profiles_json=json.dumps({
            "elevenlabs": {
                "voice": "voice-1",
                "model": "eleven_multilingual_v2",
                "base_url": "https://api.elevenlabs.io/v1",
                "region": None,
                "api_key_encrypted": "enc-eleven",
            },
        }),
    )

    ai = settings_api._public_ai_provider_profiles(row)
    tts = settings_api._public_tts_provider_profiles(row)

    assert ai["openai"]["api_key_set"] is True
    assert ai["openrouter"]["api_key_set"] is True
    assert ai["openrouter"]["model"] == "anthropic/claude-sonnet-4.6"
    assert tts["elevenlabs"]["api_key_set"] is True
    assert tts["elevenlabs"]["voice"] == "voice-1"
    combined = json.dumps({"ai": ai, "tts": tts})
    assert "enc-openai" not in combined
    assert "enc-openrouter" not in combined
    assert "enc-eleven" not in combined


def test_provider_registry_roundtrip_keeps_each_provider_configuration_separate():
    ai_registry = {}
    ai_row = SimpleNamespace(
        ai_provider="openai", ai_model="gpt-5", ai_base_url=None,
        ai_api_key_encrypted="enc-openai",
    )
    settings_api._capture_ai_provider(ai_registry, ai_row)
    ai_row.ai_provider = "openrouter"
    ai_row.ai_model = "anthropic/claude-sonnet-4.6"
    ai_row.ai_base_url = "https://openrouter.ai/api/v1"
    ai_row.ai_api_key_encrypted = "enc-openrouter"
    settings_api._capture_ai_provider(ai_registry, ai_row)

    assert ai_registry["openai"]["model"] == "gpt-5"
    assert ai_registry["openai"]["api_key_encrypted"] == "enc-openai"
    assert ai_registry["openrouter"]["model"] == "anthropic/claude-sonnet-4.6"
    assert ai_registry["openrouter"]["api_key_encrypted"] == "enc-openrouter"


@pytest.mark.asyncio
async def test_settings_provider_switch_roundtrip_restores_ai_and_tts_connections(monkeypatch):
    """Exercise the real update endpoint A→B→A with one durable settings row."""
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "openai"
    row.tts_provider = "edge_tts"

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(settings_api, "encrypt", lambda value: "enc::" + value)
    monkeypatch.setattr(
        settings_api, "decrypt_safe",
        lambda value: value.removeprefix("enc::") if value else "",
    )

    await settings_api.update_settings(settings_api.SettingsUpdate(
        ai_provider="openai", ai_model="gpt-a", ai_api_key="key-a",
    ))
    await settings_api.update_settings(settings_api.SettingsUpdate(
        ai_provider="openrouter", ai_model="anthropic/b", ai_api_key="key-b",
    ))
    restored_ai = await settings_api.update_settings(
        settings_api.SettingsUpdate(ai_provider="openai")
    )
    assert restored_ai.ai_provider == "openai"
    assert restored_ai.ai_model == "gpt-a"
    assert restored_ai.ai_api_key_set is True
    assert restored_ai.ai_provider_profiles["openrouter"]["model"] == "anthropic/b"
    assert restored_ai.ai_provider_profiles["openrouter"]["api_key_set"] is True

    await settings_api.update_settings(settings_api.SettingsUpdate(
        tts_provider="elevenlabs",
        preferred_tts_voice="voice-eleven",
        tts_model="eleven_multilingual_v2",
        tts_base_url="https://api.elevenlabs.io/v1",
        tts_api_key="tts-key-eleven",
    ))
    await settings_api.update_settings(settings_api.SettingsUpdate(
        tts_provider="edge_tts",
        preferred_tts_voice="vi-VN-HoaiMyNeural",
    ))
    restored_tts = await settings_api.update_settings(
        settings_api.SettingsUpdate(tts_provider="elevenlabs")
    )
    assert restored_tts.tts_provider == "elevenlabs"
    assert restored_tts.preferred_tts_voice == "voice-eleven"
    assert restored_tts.tts_model == "eleven_multilingual_v2"
    assert restored_tts.tts_api_key_set is True
    assert restored_tts.tts_provider_profiles["edge_tts"]["voice"] == "vi-VN-HoaiMyNeural"



@pytest.mark.asyncio
async def test_same_provider_connection_registry_keeps_distinct_endpoints(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_model = "longcat-a"
    row.ai_base_url = "http://127.0.0.1:8645/v1"
    row.ai_connection_id = "hermes-a"
    row.ai_connections_json = json.dumps({
        "hermes-a": {
            "label": "Hermes A",
            "provider": "compatible",
            "model": "longcat-a",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    result = await settings_api.upsert_ai_connection(
        settings_api.AIConnectionRequest(
            label="Hermes B",
            provider="compatible",
            model="longcat-b",
            base_url="http://127.0.0.1:8745/v1",
            activate=False,
        )
    )

    assert set(result.ai_connections) >= {"hermes-a", "hermes-b"}
    assert result.ai_connections["hermes-a"]["provider"] == "compatible"
    assert result.ai_connections["hermes-b"]["provider"] == "compatible"
    assert result.ai_connections["hermes-a"]["base_url"] != result.ai_connections["hermes-b"]["base_url"]


@pytest.mark.asyncio
async def test_connection_provider_change_never_reuses_old_provider_secret(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "openai"
    row.ai_model = "gpt-x"
    row.ai_connection_id = "cloud-main"
    row.ai_api_key_encrypted = "enc-old"
    row.ai_connections_json = json.dumps({
        "cloud-main": {
            "label": "Cloud Main",
            "provider": "openai",
            "model": "gpt-x",
            "base_url": None,
            "api_key_encrypted": "enc-old",
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(settings_api, "decrypt_safe", lambda value: "old-secret" if value else "")

    with pytest.raises(Exception):
        await settings_api.upsert_ai_connection(
            settings_api.AIConnectionRequest(
                connection_id="cloud-main",
                label="Cloud Main",
                provider="anthropic",
                model="claude-x",
                api_key=None,
            )
        )

    raw = json.loads(row.ai_connections_json)
    assert raw["cloud-main"]["provider"] == "openai"
    assert raw["cloud-main"]["api_key_encrypted"] == "enc-old"


@pytest.mark.asyncio
async def test_deleted_connection_is_archived_for_old_job_resume(monkeypatch):
    from backend.models.user_settings import UserSettings
    from backend.services import localization_service as ls

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_model = "active"
    row.ai_base_url = "http://127.0.0.1:9000/v1"
    row.ai_connection_id = "active"
    row.ai_connections_json = json.dumps({
        "active": {
            "label": "Active",
            "provider": "compatible",
            "model": "active",
            "base_url": "http://127.0.0.1:9000/v1",
            "api_key_encrypted": None,
        },
        "old-hermes": {
            "label": "Old Hermes",
            "provider": "compatible",
            "model": "old",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
        },
    })
    row.localization_profiles_json = json.dumps({"profiles": []})

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    result = await settings_api.delete_ai_connection("old-hermes")
    assert "old-hermes" not in result.ai_connections

    raw = json.loads(row.ai_connections_json)
    assert raw["old-hermes"]["archived"] is True
    saved = ls._saved_ai_connection_config(row, "old-hermes")
    assert saved["base_url"] == "http://127.0.0.1:8645/v1"


def test_tts_connection_id_can_pin_second_same_provider_account():
    from backend.core.crypto import encrypt

    row = SimpleNamespace(
        tts_connection_id="eleven-a",
        tts_provider="elevenlabs",
        preferred_tts_voice="voice-a",
        tts_model="eleven_multilingual_v2",
        tts_base_url="https://api.elevenlabs.io/v1",
        tts_region=None,
        tts_api_key_encrypted=encrypt("key-a"),
        tts_connections_json=json.dumps({
            "eleven-a": {
                "label": "EL A",
                "provider": "elevenlabs",
                "voice": "voice-a",
                "model": "eleven_multilingual_v2",
                "base_url": "https://api.elevenlabs.io/v1",
                "region": None,
                "api_key_encrypted": encrypt("key-a"),
            },
            "eleven-b": {
                "label": "EL B",
                "provider": "elevenlabs",
                "voice": "voice-b",
                "model": "eleven_multilingual_v2",
                "base_url": "https://api.elevenlabs.io/v1",
                "region": None,
                "api_key_encrypted": encrypt("key-b"),
            },
        }),
        tts_provider_profiles_json="{}",
        ai_provider=None,
        ai_api_key_encrypted=None,
        ai_connections_json="{}",
        ai_provider_profiles_json="{}",
    )
    profile = LocalizationProfile(
        id="pin-el-b",
        name="Pin EL B",
        tts_connection_id="eleven-b",
        tts_provider="elevenlabs",
    )

    runtime = localization._tts_config(row, profile, "Vietnamese")
    assert runtime.connection_id == "eleven-b"
    assert runtime.default_voice == "voice-b"
    assert runtime.api_key == "key-b"



@pytest.mark.asyncio
async def test_connection_provider_identity_is_immutable(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_connection_id = "hermes-main"
    row.ai_connections_json = json.dumps({
        "hermes-main": {
            "label": "Hermes main",
            "provider": "compatible",
            "model": "longcat",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
            "archived": False,
        },
    })
    row.tts_provider = "edge_tts"
    row.tts_connection_id = "edge-main"
    row.tts_connections_json = json.dumps({
        "edge-main": {
            "label": "Edge main",
            "provider": "edge_tts",
            "voice": "vi-VN-HoaiMyNeural",
            "model": None,
            "base_url": None,
            "region": None,
            "api_key_encrypted": None,
            "archived": False,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    with pytest.raises(Exception) as ai_exc:
        await settings_api.upsert_ai_connection(settings_api.AIConnectionRequest(
            connection_id="hermes-main",
            label="Same ID, different transport",
            provider="openai",
            model="gpt-5",
            api_key="secret",
        ))
    assert getattr(ai_exc.value, "status_code", None) == 409
    assert "immutable" in str(getattr(ai_exc.value, "detail", "")).lower()
    assert json.loads(row.ai_connections_json)["hermes-main"]["provider"] == "compatible"

    with pytest.raises(Exception) as tts_exc:
        await settings_api.upsert_tts_connection(settings_api.TTSConnectionRequest(
            connection_id="edge-main",
            label="Same ID, different TTS",
            provider="elevenlabs",
            voice="voice-x",
            model="eleven_multilingual_v2",
            api_key="secret",
        ))
    assert getattr(tts_exc.value, "status_code", None) == 409
    assert "immutable" in str(getattr(tts_exc.value, "detail", "")).lower()
    assert json.loads(row.tts_connections_json)["edge-main"]["provider"] == "edge_tts"



@pytest.mark.asyncio
async def test_archived_connection_id_cannot_be_reused(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_connection_id = "active-ai"
    row.ai_connections_json = json.dumps({
        "active-ai": {
            "label": "Active",
            "provider": "compatible",
            "model": "live",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
            "archived": False,
        },
        "old-hermes": {
            "label": "Old Hermes",
            "provider": "compatible",
            "model": "old",
            "base_url": "http://127.0.0.1:9000/v1",
            "api_key_encrypted": None,
            "archived": True,
        },
    })
    row.tts_provider = "edge_tts"
    row.tts_connection_id = "edge-active"
    row.tts_connections_json = json.dumps({
        "edge-active": {
            "label": "Edge active",
            "provider": "edge_tts",
            "voice": "vi-VN-HoaiMyNeural",
            "model": None,
            "base_url": None,
            "region": None,
            "api_key_encrypted": None,
            "archived": False,
        },
        "old-tts": {
            "label": "Old TTS",
            "provider": "edge_tts",
            "voice": "en-US-AndrewMultilingualNeural",
            "model": None,
            "base_url": None,
            "region": None,
            "api_key_encrypted": None,
            "archived": True,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    with pytest.raises(Exception) as ai_exc:
        await settings_api.upsert_ai_connection(settings_api.AIConnectionRequest(
            connection_id="old-hermes",
            label="Reused old ID",
            provider="compatible",
            model="new",
            base_url="http://127.0.0.1:9999/v1",
        ))
    assert getattr(ai_exc.value, "status_code", None) == 409
    assert "archived" in str(getattr(ai_exc.value, "detail", "")).lower()

    with pytest.raises(Exception) as tts_exc:
        await settings_api.upsert_tts_connection(settings_api.TTSConnectionRequest(
            connection_id="old-tts",
            label="Reused old TTS ID",
            provider="edge_tts",
            voice="vi-VN-HoaiMyNeural",
        ))
    assert getattr(tts_exc.value, "status_code", None) == 409
    assert "archived" in str(getattr(tts_exc.value, "detail", "")).lower()

    assert json.loads(row.ai_connections_json)["old-hermes"]["base_url"] == "http://127.0.0.1:9000/v1"
    assert json.loads(row.tts_connections_json)["old-tts"]["voice"] == "en-US-AndrewMultilingualNeural"



@pytest.mark.asyncio
async def test_archived_or_conflicting_connection_cannot_be_activated(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_connection_id = "active-ai"
    row.ai_connections_json = json.dumps({
        "active-ai": {
            "label": "Active AI",
            "provider": "compatible",
            "model": "longcat",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
            "archived": False,
        },
        "archived-ai": {
            "label": "Archived AI",
            "provider": "compatible",
            "model": "old",
            "base_url": "http://127.0.0.1:9000/v1",
            "api_key_encrypted": None,
            "archived": True,
        },
    })
    row.tts_provider = "edge_tts"
    row.tts_connection_id = "edge-active"
    row.tts_connections_json = json.dumps({
        "edge-active": {
            "label": "Edge active",
            "provider": "edge_tts",
            "voice": "vi-VN-HoaiMyNeural",
            "model": None,
            "base_url": None,
            "region": None,
            "api_key_encrypted": None,
            "archived": False,
        },
        "archived-tts": {
            "label": "Old edge",
            "provider": "edge_tts",
            "voice": "en-US-AndrewMultilingualNeural",
            "model": None,
            "base_url": None,
            "region": None,
            "api_key_encrypted": None,
            "archived": True,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    with pytest.raises(Exception) as archived_ai:
        await settings_api.update_settings(settings_api.SettingsUpdate(
            ai_connection_id="archived-ai",
        ))
    assert getattr(archived_ai.value, "status_code", None) == 409
    assert "archived" in str(getattr(archived_ai.value, "detail", "")).lower()
    assert row.ai_connection_id == "active-ai"

    with pytest.raises(Exception) as conflicting_ai:
        await settings_api.update_settings(settings_api.SettingsUpdate(
            ai_connection_id="active-ai",
            ai_provider="openai",
        ))
    assert getattr(conflicting_ai.value, "status_code", None) == 409
    assert "conflicting" in str(getattr(conflicting_ai.value, "detail", "")).lower()

    with pytest.raises(Exception) as archived_tts:
        await settings_api.update_settings(settings_api.SettingsUpdate(
            tts_connection_id="archived-tts",
        ))
    assert getattr(archived_tts.value, "status_code", None) == 409
    assert "archived" in str(getattr(archived_tts.value, "detail", "")).lower()
    assert row.tts_connection_id == "edge-active"

    with pytest.raises(Exception) as conflicting_tts:
        await settings_api.update_settings(settings_api.SettingsUpdate(
            tts_connection_id="edge-active",
            tts_provider="elevenlabs",
        ))
    assert getattr(conflicting_tts.value, "status_code", None) == 409
    assert "conflicting" in str(getattr(conflicting_tts.value, "detail", "")).lower()



@pytest.mark.asyncio
async def test_tts_preview_connection_id_supplies_saved_runtime_fields(monkeypatch, tmp_path):
    from backend.api import tts_preview

    saved = SimpleNamespace()
    connection = {
        "provider": "openai_compatible_tts",
        "voice": "vi-test",
        "model": "tts-local",
        "base_url": "http://127.0.0.1:9001/v1",
        "region": None,
        "api_key_encrypted": None,
    }
    seen = {}

    monkeypatch.setattr(settings_api, "_load_saved_settings", lambda: _async_value(saved))
    monkeypatch.setattr(
        settings_api,
        "_tts_probe_connection",
        lambda _saved, body: connection if body.connection_id == "local-tts" else {},
    )
    monkeypatch.setattr(settings_api, "_tts_probe_key", lambda body: _async_value(""))

    async def fake_generate(text, **kwargs):
        seen.update(kwargs)
        kwargs["output_path"].write_bytes(b"RIFFfake")
        return kwargs["output_path"]

    monkeypatch.setattr(tts_service, "generate_tts", fake_generate)
    monkeypatch.setattr(type(tts_preview.settings), "TMP_DIR", property(lambda _self: tmp_path))

    response = await tts_preview.tts_preview(tts_preview._PreviewBody(
        connection_id="local-tts",
        provider="openai_compatible_tts",
        voice_id="vi-test",
    ))

    assert response.media_type == "audio/wav"
    assert seen["model"] == "tts-local"
    assert seen["base_url"] == "http://127.0.0.1:9001/v1"



@pytest.mark.asyncio
async def test_provider_only_switch_rejects_ambiguous_same_provider_connections(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "openai"
    row.ai_connection_id = "openai-main"
    row.ai_connections_json = json.dumps({
        "openai-main": {
            "label": "OpenAI",
            "provider": "openai",
            "model": "gpt-5",
            "base_url": None,
            "api_key_encrypted": "enc-openai",
            "archived": False,
        },
        "hermes-a": {
            "label": "Hermes A",
            "provider": "compatible",
            "model": "longcat-a",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
            "archived": False,
        },
        "hermes-b": {
            "label": "Hermes B",
            "provider": "compatible",
            "model": "longcat-b",
            "base_url": "http://127.0.0.1:8646/v1",
            "api_key_encrypted": None,
            "archived": False,
        },
    })
    row.tts_provider = "edge_tts"
    row.tts_connection_id = "edge-main"
    row.tts_connections_json = json.dumps({
        "edge-main": {
            "label": "Edge",
            "provider": "edge_tts",
            "voice": "vi-VN-HoaiMyNeural",
            "model": None,
            "base_url": None,
            "region": None,
            "api_key_encrypted": None,
            "archived": False,
        },
        "el-a": {
            "label": "Eleven A",
            "provider": "elevenlabs",
            "voice": "voice-a",
            "model": "eleven_multilingual_v2",
            "base_url": "https://api.elevenlabs.io/v1",
            "region": None,
            "api_key_encrypted": "enc-a",
            "archived": False,
        },
        "el-b": {
            "label": "Eleven B",
            "provider": "elevenlabs",
            "voice": "voice-b",
            "model": "eleven_multilingual_v2",
            "base_url": "https://api.elevenlabs.io/v1",
            "region": None,
            "api_key_encrypted": "enc-b",
            "archived": False,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    with pytest.raises(Exception) as ai_exc:
        await settings_api.update_settings(settings_api.SettingsUpdate(
            ai_provider="compatible",
        ))
    assert getattr(ai_exc.value, "status_code", None) == 409
    assert "ai_connection_id" in str(getattr(ai_exc.value, "detail", ""))

    with pytest.raises(Exception) as tts_exc:
        await settings_api.update_settings(settings_api.SettingsUpdate(
            tts_provider="elevenlabs",
        ))
    assert getattr(tts_exc.value, "status_code", None) == 409
    assert "tts_connection_id" in str(getattr(tts_exc.value, "detail", ""))

    assert row.ai_connection_id == "openai-main"
    assert row.tts_connection_id == "edge-main"


@pytest.mark.asyncio
async def test_partial_connection_edit_preserves_unsent_runtime_fields(monkeypatch):
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_connection_id = "hermes-a"
    row.ai_model = "longcat-a"
    row.ai_base_url = "http://127.0.0.1:8645/v1"
    row.ai_connections_json = json.dumps({
        "hermes-a": {
            "label": "Hermes A",
            "provider": "compatible",
            "model": "longcat-a",
            "base_url": "http://127.0.0.1:8645/v1",
            "api_key_encrypted": None,
            "archived": False,
        },
    })
    row.tts_provider = "edge_tts"
    row.tts_connection_id = "edge-main"
    row.preferred_tts_voice = "vi-VN-HoaiMyNeural"
    row.tts_model = "edge-model-marker"
    row.tts_region = "legacy-region"
    row.tts_connections_json = json.dumps({
        "edge-main": {
            "label": "Edge Main",
            "provider": "edge_tts",
            "voice": "vi-VN-HoaiMyNeural",
            "model": "edge-model-marker",
            "base_url": None,
            "region": "legacy-region",
            "api_key_encrypted": None,
            "archived": False,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())

    await settings_api.upsert_ai_connection(settings_api.AIConnectionRequest(
        connection_id="hermes-a",
        label="Hermes renamed",
        provider="compatible",
        activate=False,
    ))
    ai_saved = json.loads(row.ai_connections_json)["hermes-a"]
    assert ai_saved["model"] == "longcat-a"
    assert ai_saved["base_url"] == "http://127.0.0.1:8645/v1"

    await settings_api.upsert_tts_connection(settings_api.TTSConnectionRequest(
        connection_id="edge-main",
        label="Edge renamed",
        provider="edge_tts",
        activate=False,
    ))
    tts_saved = json.loads(row.tts_connections_json)["edge-main"]
    assert tts_saved["voice"] == "vi-VN-HoaiMyNeural"
    assert tts_saved["model"] == "edge-model-marker"
    assert tts_saved["region"] == "legacy-region"


@pytest.mark.asyncio
async def test_ai_connection_probe_has_hard_timeout_and_disables_sdk_retry(monkeypatch):
    class HangingClient:
        def __init__(self):
            self.max_retries = None

        async def chat(self, **kwargs):
            await __import__("asyncio").Event().wait()

    client = HangingClient()

    async def no_saved_settings():
        raise AssertionError("unsaved AI probe must not read saved settings")

    monkeypatch.setattr(settings_api, "_load_saved_settings", no_saved_settings)
    monkeypatch.setattr(settings_api, "_AI_PROBE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        "backend.core.ai_provider.make_ai_client",
        lambda **kwargs: client,
    )

    with pytest.raises(Exception) as exc:
        await settings_api.test_ai_connection(settings_api.AIProbeRequest(
            provider="compatible",
            model="qwen3:8b",
            base_url="http://127.0.0.1:1234/v1",
        ))

    assert getattr(exc.value, "status_code", None) == 408
    assert "timed out" in str(getattr(exc.value, "detail", "")).lower()
    assert client.max_retries == 0


@pytest.mark.asyncio
async def test_explicit_invalid_ai_clear_is_rejected_without_mutating_saved_connection(monkeypatch):
    from backend.core.crypto import encrypt
    from backend.models.user_settings import UserSettings

    row = UserSettings(user_id="local")
    row.ai_provider = "compatible"
    row.ai_connection_id = "cloud-compatible"
    row.ai_model = "model-x"
    row.ai_base_url = "https://api.example.com/v1"
    row.ai_api_key_encrypted = encrypt("saved-key")
    row.ai_connections_json = json.dumps({
        "cloud-compatible": {
            "label": "Cloud Compatible",
            "provider": "compatible",
            "model": "model-x",
            "base_url": "https://api.example.com/v1",
            "api_key_encrypted": row.ai_api_key_encrypted,
            "archived": False,
        },
    })

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()
        def add(self, _row):
            return None
        async def commit(self):
            return None

    monkeypatch.setattr(settings_api, "AsyncSessionLocal", lambda: FakeSession())
    before = row.ai_connections_json

    with pytest.raises(Exception) as exc:
        await settings_api.upsert_ai_connection(settings_api.AIConnectionRequest(
            connection_id="cloud-compatible",
            label="Cloud Compatible",
            provider="compatible",
            model=None,
            activate=False,
        ))

    assert getattr(exc.value, "status_code", None) == 400
    assert "model" in str(getattr(exc.value, "detail", "")).lower()
    assert row.ai_connections_json == before


@pytest.mark.asyncio
async def test_saved_ai_probe_cannot_reinterpret_connection_provider_or_expose_secret(monkeypatch):
    from backend.core.crypto import encrypt

    saved = SimpleNamespace(
        ai_provider=None,
        ai_connection_id=None,
        ai_model=None,
        ai_base_url=None,
        ai_api_key_encrypted=None,
        ai_provider_profiles_json="{}",
        ai_connections_json=json.dumps({
            "openai-main": {
                "label": "OpenAI Main",
                "provider": "openai",
                "model": "gpt-5",
                "base_url": "https://api.openai.com/v1",
                "api_key_encrypted": encrypt("saved-openai-secret"),
                "archived": False,
            },
        }),
    )

    async def load_saved():
        return saved

    monkeypatch.setattr(settings_api, "_load_saved_settings", load_saved)

    body = settings_api.AIProbeRequest(
        connection_id="openai-main",
        provider="compatible",
        model="attacker-model",
        base_url="https://attacker.invalid/v1",
    )

    with pytest.raises(Exception) as exc:
        await settings_api._probe_key(body)

    assert getattr(exc.value, "status_code", None) == 409
    assert "bound to provider" in str(getattr(exc.value, "detail", "")).lower()
    assert "saved-openai-secret" not in str(exc.value)


def test_saved_tts_probe_cannot_reinterpret_connection_provider():
    saved = SimpleNamespace(
        tts_provider=None,
        tts_connection_id=None,
        preferred_tts_voice=None,
        tts_model=None,
        tts_base_url=None,
        tts_region=None,
        tts_api_key_encrypted=None,
        tts_provider_profiles_json="{}",
        tts_connections_json=json.dumps({
            "eleven-main": {
                "label": "Eleven Main",
                "provider": "elevenlabs",
                "voice": "voice-a",
                "model": "eleven_multilingual_v2",
                "base_url": "https://api.elevenlabs.io/v1",
                "region": None,
                "api_key_encrypted": "encrypted-secret",
                "archived": False,
            },
        }),
    )

    with pytest.raises(Exception) as exc:
        settings_api._tts_probe_connection(
            saved,
            settings_api.TTSProbeRequest(
                connection_id="eleven-main",
                provider="openai_compatible_tts",
                base_url="https://attacker.invalid/v1",
            ),
        )

    assert getattr(exc.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_unsaved_keyless_tts_probe_never_borrows_saved_credentials(monkeypatch):
    async def no_db_lookup():
        raise AssertionError("new keyless TTS form must not load saved settings")

    monkeypatch.setattr(settings_api, "_load_saved_tts_settings", no_db_lookup)

    assert await settings_api._tts_probe_key(settings_api.TTSProbeRequest(
        provider="edge_tts",
    )) == ""
    assert await settings_api._tts_probe_key(settings_api.TTSProbeRequest(
        provider="gpt_sovits",
        base_url="http://127.0.0.1:9880",
    )) == ""


@pytest.mark.asyncio
async def test_unsaved_openai_tts_probe_uses_only_environment_fallback(monkeypatch):
    async def no_db_lookup():
        raise AssertionError("new OpenAI TTS form must not load saved settings")

    monkeypatch.setattr(settings_api, "_load_saved_tts_settings", no_db_lookup)
    monkeypatch.setattr(settings_api.app_settings, "OPENAI_API_KEY", "env-openai-key")

    assert await settings_api._tts_probe_key(settings_api.TTSProbeRequest(
        provider="openai_tts",
    )) == "env-openai-key"


@pytest.mark.asyncio
async def test_unsaved_keyless_local_ai_probe_never_reads_saved_credentials(monkeypatch):
    async def no_saved_settings():
        raise AssertionError("new local AI form must not load saved settings")

    monkeypatch.setattr(settings_api, "_load_saved_settings", no_saved_settings)

    assert await settings_api._probe_key(settings_api.AIProbeRequest(
        provider="lmstudio",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
    )) == ""
