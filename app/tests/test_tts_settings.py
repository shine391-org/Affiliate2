# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.api import settings as settings_api
from backend.services import elevenlabs_tts_service as eleven
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
async def test_settings_tts_test_uses_unsaved_plaintext_key(monkeypatch):
    seen = {}

    async def fake_test(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "provider": "elevenlabs", "authenticated": True, "message": "ready"}

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
async def test_settings_tts_test_uses_saved_encrypted_key_only_for_saved_provider(monkeypatch):
    saved = SimpleNamespace(
        tts_provider="elevenlabs",
        tts_api_key_encrypted="encrypted-value",
    )
    monkeypatch.setattr(settings_api, "_load_saved_tts_settings", lambda: _async_value(saved))
    monkeypatch.setattr(settings_api, "decrypt_safe", lambda value: "stored-secret")

    same = await settings_api._tts_probe_key(settings_api.TTSProbeRequest(provider="elevenlabs"))
    different = await settings_api._tts_probe_key(settings_api.TTSProbeRequest(provider="azure_tts"))

    assert same == "stored-secret"
    assert different == ""


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
