# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Focused coverage for the Groq ASR adapter.

Two layers, both real:

1. Unit tests drive ``transcribe_groq`` against an in-process HTTP server that
   speaks the actual ``multipart/form-data`` Groq contract — so the request the
   adapter builds (endpoint, bearer auth, model, ``verbose_json``,
   ``timestamp_granularities[]``, the file part) is inspected from real wire
   bytes, not a mock's assumptions.
2. The integration tests shell out to real ffmpeg to produce a real FLAC and
   POST it to the local server. No Groq credential is available here, so the
   server is local; this proves transport + normalization, NOT Groq's live
   transcription quality — that remains unverified.

Retry/Retry-After tests use real 429/5xx responses from the in-process server
and assert the adapter never shortens a server-provided delay.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from backend.core.crypto import encrypt
from backend.services import groq_asr_service as groq


# ── fake settings row with a saved Groq connection ───────────────────────────

def _settings_row(*, base_url="https://api.groq.com/openai/v1", key="groq-secret",
                  provider="compatible", connection_id="groq-asr"):
    connections = {
        connection_id: {
            "label": "Groq ASR",
            "provider": provider,
            "model": "whisper-large-v3-turbo",
            "base_url": base_url,
            "api_key_encrypted": encrypt(key) if key else None,
        },
    }
    return SimpleNamespace(
        ai_provider=provider,
        ai_connection_id=connection_id,
        ai_model="whisper-large-v3-turbo",
        ai_base_url=base_url,
        ai_api_key_encrypted=encrypt(key) if key else None,
        ai_connections_json=json.dumps(connections),
        ai_provider_profiles_json="{}",
    )


def _profile(**overrides):
    data = {
        "id": "groq-fast",
        "name": "Groq Fast",
        "asr_model": "whisper-large-v3-turbo",
        "asr_connection_id": "groq-asr",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


# ── in-process Groq-shaped server ────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeGroq/1"

    def log_message(self, *args):  # keep test output clean
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length)
        record = {
            "path": self.path,
            "authorization": self.headers.get("authorization"),
            "content_type": self.headers.get("content-type"),
            "body": body,
        }
        with self.server.lock:
            self.server.requests.append(record)
            index = len(self.server.requests) - 1
            responses = self.server.responses
            response = responses[min(index, len(responses) - 1)]

        if isinstance(response, tuple):
            status, headers, payload = response
        else:
            status, headers, payload = response, {}, None

        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        if payload is None:
            payload = {"error": {"message": "boom", "type": "server_error"}}
        raw = json.dumps(payload).encode()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _FakeGroq:
    def __init__(self, responses):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.lock = threading.Lock()
        self._server.requests = []
        self._server.responses = list(responses)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)

    @property
    def base_url(self):
        host, port = self._server.server_address
        return f"http://{host}:{port}/openai/v1"

    @property
    def requests(self):
        with self._server.lock:
            return list(self._server.requests)


def _verbose_json(**overrides):
    payload = {
        "text": "hello world",
        "language": "en",
        "duration": 2.0,
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": " hello world ",
             "words": [
                 {"word": "hello", "start": 0.0, "end": 1.0, "probability": 0.9},
                 {"word": "world", "start": 1.0, "end": 2.0, "probability": 0.8},
             ]},
        ],
        "words": [
            {"word": "hello", "start": 0.0, "end": 1.0},
            {"word": "world", "start": 1.0, "end": 2.0},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("payload", [
    {"text": "missing timings", "segments": []},
    {"text": "bad bounds", "duration": 1, "segments": [{"text":"bad", "start":0, "end":2}]},
    {"text": "no word timings", "segments": [{"text":"bad", "start":0, "end":2}]},
])
def test_response_without_valid_timeline_and_words_is_rejected(payload):
    with pytest.raises(groq.GroqASRError):
        groq._to_transcript(payload, model="whisper-large-v3-turbo", upload_bytes=1, connection_id="saved")


@pytest.mark.asyncio
async def test_real_ffmpeg_truncated_audio_is_rejected_and_cleaned(tmp_path, monkeypatch):
    import shutil
    import wave
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    source = tmp_path / "truncated.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 32000)
    # Header still advertises the full payload; the real decoder detects loss.
    source.write_bytes(source.read_bytes()[:32044])
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(groq, "settings", SimpleNamespace(TMP_DIR=scratch))
    with pytest.raises(groq.GroqASRError, match="extraction failed"):
        await groq.extract_mono16k_flac(source)
    assert not list(scratch.glob("groq_asr_*"))


def test_timeline_must_fit_the_actual_uploaded_audio_not_only_provider_duration():
    with pytest.raises(groq.GroqASRError, match="duration|bounds"):
        groq._to_transcript(_verbose_json(), model="whisper-large-v3-turbo", upload_bytes=1, connection_id="saved", audio_duration_s=1)


def test_cross_boundary_words_are_not_dropped_or_duplicated():
    payload = {"text":"hello world", "duration":2, "segments":[{"start":0,"end":1,"text":"hello"},{"start":1,"end":2,"text":"world"}], "words":[{"word":"world","start":1.1,"end":2},{"word":"hello","start":0,"end":1.05}]}
    tx = groq._to_transcript(payload, model="whisper-large-v3-turbo", upload_bytes=1, connection_id="saved")
    assert [w["word"] for s in tx.segments for w in s.source_words] == ["hello", "world"]


def test_zero_duration_word_at_sentence_boundary_belongs_to_next_sentence():
    payload = {"text":"hello world", "duration":2, "segments":[{"start":0,"end":1,"text":"hello"},{"start":1,"end":2,"text":"world"}], "words":[{"word":"hello","start":0,"end":1},{"word":"world","start":1,"end":1}]}
    tx = groq._to_transcript(payload, model="whisper-large-v3-turbo", upload_bytes=1, connection_id="saved")
    assert [[w["word"] for w in s.source_words] for s in tx.segments] == [["hello"],["world"]]


def test_live_short_utterance_with_overlapping_word_timing_keeps_both_sentences():
    payload={"duration":2,"text":"我可以拍可以","segments":[{"start":0,"end":1.32,"text":"我可以拍"},{"start":1.32,"end":1.72,"text":"可以"}],"words":[{"word":"我","start":.62,"end":.86},{"word":"可以","start":.86,"end":1},{"word":"拍","start":1,"end":1.2},{"word":"可以","start":.82,"end":1.64}]}
    tx=groq._to_transcript(payload, model="whisper-large-v3-turbo", upload_bytes=1, connection_id="saved")
    assert [[w["word"] for w in s.source_words] for s in tx.segments] == [["我","可以","拍"],["可以"]]


@pytest.mark.asyncio
async def test_total_budget_bounds_even_a_slow_streaming_http_request(fake_extract, monkeypatch):
    async def slow_post(*args, **kwargs):
        await asyncio.sleep(1)
        return None
    monkeypatch.setattr(groq.httpx.AsyncClient, "post", slow_post)
    monkeypatch.setattr(groq, "TOTAL_TIMEOUT_S", 0.05)
    with pytest.raises(groq.GroqASRError, match="budget|timed out"):
        await asyncio.wait_for(groq._post_transcription("http://127.0.0.1", "test", "whisper-large-v3-turbo", fake_extract, None), 0.5)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Isolate scratch + allow the loopback test server as the Groq host.

    ``settings.TMP_DIR`` is redirected so temp cleanup is observable, and
    ``GROQ_API_HOST`` is repointed at the loopback test server so the adapter's
    real host guard is exercised against a reachable endpoint instead of being
    bypassed. Production still refuses any non-``api.groq.com`` host.
    """
    path = tmp_path / "tmp"
    monkeypatch.setattr(
        type(groq.settings), "TMP_DIR", property(lambda _self: path),
    )
    monkeypatch.setattr(groq, "GROQ_API_HOST", "127.0.0.1")
    return path


@pytest.fixture
def fake_extract(scratch, monkeypatch):
    """Patch ffmpeg extraction with a fake FLAC file; return its path."""
    out = scratch / "extracted.flac"
    out.parent.mkdir(parents=True, exist_ok=True)
    import soundfile as sf
    import numpy as np
    sf.write(out, np.zeros(64000), 16000, format="FLAC")

    async def _extract(_media):
        return out

    monkeypatch.setattr(groq, "extract_mono16k_flac", _extract)
    return out


def _record_sleep(sink):
    async def _sleep(seconds):
        sink.append(seconds)
    return _sleep


# ── endpoint identity ────────────────────────────────────────────────────────

def test_canonicalize_normalizes_official_groq_host():
    assert groq.canonicalize_groq_base_url("https://api.groq.com") == "https://api.groq.com/openai/v1"
    assert groq.canonicalize_groq_base_url("https://api.groq.com/") == "https://api.groq.com/openai/v1"
    assert groq.canonicalize_groq_base_url("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1"
    assert groq.canonicalize_groq_base_url("https://api.groq.com/openai/v1/") == "https://api.groq.com/openai/v1"


def test_transcriptions_url_is_the_documented_endpoint():
    assert groq.transcriptions_url("https://api.groq.com") == (
        "https://api.groq.com/openai/v1/audio/transcriptions"
    )


def test_non_groq_endpoint_is_refused_not_invented():
    with pytest.raises(groq.GroqASRError) as exc:
        groq.canonicalize_groq_base_url("https://api.openai.com/v1")
    assert exc.value.kind == "connection"
    assert exc.value.fallback_eligible is False


def test_empty_endpoint_is_refused():
    with pytest.raises(groq.GroqASRError) as exc:
        groq.canonicalize_groq_base_url(None)
    assert exc.value.kind == "connection"


# ── connection resolution ────────────────────────────────────────────────────

def test_resolve_uses_saved_encrypted_connection():
    row = _settings_row(key="super-secret")
    base_url, api_key = groq.resolve_groq_connection(row, "groq-asr")
    assert base_url == "https://api.groq.com/openai/v1"
    assert api_key == "super-secret"


def test_resolve_missing_connection_is_terminal():
    row = _settings_row()
    with pytest.raises(groq.GroqASRError) as exc:
        groq.resolve_groq_connection(row, "does-not-exist")
    assert exc.value.kind == "connection"
    assert exc.value.fallback_eligible is False


def test_resolve_non_compatible_provider_is_refused():
    row = _settings_row(provider="openai")
    with pytest.raises(groq.GroqASRError) as exc:
        groq.resolve_groq_connection(row, "groq-asr")
    assert exc.value.kind == "connection"


def test_resolve_missing_key_is_credential_error():
    row = _settings_row(key="")
    with pytest.raises(groq.GroqASRError) as exc:
        groq.resolve_groq_connection(row, "groq-asr")
    assert exc.value.kind == "credential"


# ── happy path over a real HTTP contract ─────────────────────────────────────

async def test_transcribe_builds_documented_multipart_request(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video-bytes")

    with _FakeGroq([(200, {}, _verbose_json())]) as server:
        row = _settings_row(base_url=server.base_url)
        result = await groq.transcribe_groq(media, _profile(), row, language="en")

        assert len(server.requests) == 1
        request = server.requests[0]
        assert request["path"] == "/openai/v1/audio/transcriptions"
        assert request["authorization"] == "Bearer groq-secret"
        assert request["content_type"].startswith("multipart/form-data")
        body = request["body"]
        assert b'name="model"' in body and b"whisper-large-v3-turbo" in body
        assert b'name="response_format"' in body and b"verbose_json" in body
        assert body.count(b'name="timestamp_granularities[]"') == 2
        # httpx serializes a list field as repeated parts: the VALUES are the
        # granularities, carried under the timestamp_granularities[] name.
        assert b"\r\n\r\nword\r\n" in body and b"\r\n\r\nsegment\r\n" in body
        assert b'name="language"' in body and b"en" in body
        assert b'name="file"; filename="extracted.flac"' in body

    assert result.source == "groq"
    assert result.language == "en"
    assert result.metadata["provider"] == "groq"
    assert result.metadata["model"] == "whisper-large-v3-turbo"
    assert result.metadata["connection_id"] == "groq-asr"


async def test_transcribe_normalizes_segments_and_words(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video-bytes")

    with _FakeGroq([(200, {}, _verbose_json())]) as server:
        row = _settings_row(base_url=server.base_url)
        result = await groq.transcribe_groq(media, _profile(), row)

    assert result.text == "hello world"
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.source_start_ms == 0
    assert segment.source_end_ms == 2000
    assert segment.source_text == "hello world"
    assert [w["word"] for w in segment.source_words] == ["hello", "world"]
    # Words stay in seconds to match canonical whisper_service output.
    assert segment.source_words[0]["start"] == 0.0
    assert segment.source_words[1]["end"] == 2.0


async def test_transcribe_orders_out_of_order_segments(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    payload = _verbose_json(segments=[
        {"start": 3.0, "end": 4.0, "text": "second"},
        {"start": 0.0, "end": 2.0, "text": "first"},
    ], duration=4, words=[{"start":0, "end":2, "word":"first"}, {"start":3, "end":4, "word":"second"}])

    with _FakeGroq([(200, {}, payload)]) as server:
        row = _settings_row(base_url=server.base_url)
        result = await groq.transcribe_groq(media, _profile(), row)

    assert [s.source_text for s in result.segments] == ["first", "second"]
    assert [s.source_start_ms for s in result.segments] == [0, 3000]


async def test_transcribe_rejects_non_finite_timestamps(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    payload = _verbose_json(segments=[{"start": "NaN", "end": 2.0, "text": "bad"}], words=[])

    with _FakeGroq([(200, {}, payload)]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
    assert exc.value.kind == "invalid_response"
    assert exc.value.fallback_eligible is False


async def test_transcribe_rejects_negative_timestamp(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    payload = _verbose_json(segments=[{"start": -1.0, "end": 2.0, "text": "bad"}], words=[])

    with _FakeGroq([(200, {}, payload)]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
    assert exc.value.kind == "invalid_response"


async def test_transcribe_rejects_end_before_start(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    payload = _verbose_json(segments=[{"start": 2.0, "end": 1.0, "text": "bad"}], words=[])

    with _FakeGroq([(200, {}, payload)]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
    assert exc.value.kind == "invalid_response"


async def test_transcribe_empty_transcript_is_reported(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")

    with _FakeGroq([(200, {}, {"text": "", "segments": [], "words": []})]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
    assert exc.value.kind == "empty_transcript"


# ── credential / terminal failures ───────────────────────────────────────────

async def test_credential_401_is_terminal_without_retry(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")

    with _FakeGroq([(401, {}, {"error": {"message": "invalid key"}})]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == 1  # no retry
    assert exc.value.status_code == 401
    assert exc.value.kind == "credential"
    assert exc.value.fallback_eligible is False
    assert exc.value.retryable is False
    assert "groq-secret" not in str(exc.value)


async def test_credential_403_is_terminal(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")

    with _FakeGroq([(403, {}, {"error": {"message": "forbidden"}})]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == 1
    assert exc.value.kind == "credential"
    assert exc.value.fallback_eligible is False


async def test_bad_request_400_is_terminal_without_retry(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")

    with _FakeGroq([(400, {}, {"error": {"message": "bad model"}})]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == 1
    assert exc.value.kind == "request"
    assert exc.value.fallback_eligible is False


# ── transient retry behavior ─────────────────────────────────────────────────

async def test_retries_then_succeeds_on_5xx(scratch, fake_extract, tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    slept = []
    monkeypatch.setattr(groq, "_sleep", _record_sleep(slept))

    responses = [
        (503, {}, {"error": {"message": "unavailable"}}),
        (200, {}, _verbose_json()),
    ]
    with _FakeGroq(responses) as server:
        row = _settings_row(base_url=server.base_url)
        result = await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == 2
    assert result.text == "hello world"
    assert slept == [groq.RETRY_BACKOFF_S]


async def test_exhausts_retry_budget_on_repeated_5xx(scratch, fake_extract, tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    slept = []
    monkeypatch.setattr(groq, "_sleep", _record_sleep(slept))

    with _FakeGroq([(500, {}, {"error": {"message": "boom"}})]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == groq.MAX_ATTEMPTS  # 1 + 3 retries
    assert exc.value.fallback_eligible is True
    assert len(slept) == groq.MAX_RETRIES


async def test_honors_retry_after_seconds_without_shortening(
    scratch, fake_extract, tmp_path, monkeypatch,
):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    slept = []
    monkeypatch.setattr(groq, "_sleep", _record_sleep(slept))

    responses = [
        (429, {"Retry-After": "7"}, {"error": {"message": "slow down"}}),
        (200, {}, _verbose_json()),
    ]
    with _FakeGroq(responses) as server:
        row = _settings_row(base_url=server.base_url)
        result = await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == 2
    assert result.text == "hello world"
    # The server's 7s delay is honored, not shortened to the 1s default.
    assert slept == [7.0]


async def test_honors_retry_after_http_date(scratch, fake_extract, tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    slept = []
    monkeypatch.setattr(groq, "_sleep", _record_sleep(slept))

    when = datetime.now(timezone.utc) + timedelta(seconds=9)
    responses = [
        (429, {"Retry-After": format_datetime(when, usegmt=True)},
         {"error": {"message": "slow down"}}),
        (200, {}, _verbose_json()),
    ]
    with _FakeGroq(responses) as server:
        row = _settings_row(base_url=server.base_url)
        await groq.transcribe_groq(media, _profile(), row)

    assert len(slept) == 1
    assert 7.0 <= slept[0] <= 9.5  # parsed from the HTTP date, ~9s


async def test_insufficient_budget_fails_instead_of_shortening_delay(
    scratch, fake_extract, tmp_path, monkeypatch,
):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    slept = []
    monkeypatch.setattr(groq, "_sleep", _record_sleep(slept))
    # Total budget already spent → a Retry-After delay cannot fit.
    monkeypatch.setattr(groq, "TOTAL_TIMEOUT_S", 0.25)

    responses = [
        (429, {"Retry-After": "30"}, {"error": {"message": "slow down"}}),
    ]
    with _FakeGroq(responses) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
        assert len(server.requests) == 1

    assert slept == []
    assert "budget" in str(exc.value).lower()
    assert exc.value.fallback_eligible is True


# ── upload size guard ────────────────────────────────────────────────────────

async def test_oversized_upload_is_rejected_without_chunking(
    scratch, fake_extract, tmp_path, monkeypatch,
):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    monkeypatch.setattr(groq, "FREE_TIER_MAX_UPLOAD_BYTES", 4)

    with _FakeGroq([(200, {}, _verbose_json())]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError) as exc:
            await groq.transcribe_groq(media, _profile(), row)
        assert server.requests == []  # never uploaded

    assert exc.value.kind == "too_large"
    assert exc.value.fallback_eligible is False


# ── temp-file lifecycle ──────────────────────────────────────────────────────

async def test_temp_audio_removed_on_success(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")

    with _FakeGroq([(200, {}, _verbose_json())]) as server:
        row = _settings_row(base_url=server.base_url)
        await groq.transcribe_groq(media, _profile(), row)

    assert fake_extract.exists() is False
    assert list(scratch.glob("*.flac")) == []


async def test_temp_audio_removed_on_failure(scratch, fake_extract, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")

    with _FakeGroq([(401, {}, {"error": {"message": "no"}})]) as server:
        row = _settings_row(base_url=server.base_url)
        with pytest.raises(groq.GroqASRError):
            await groq.transcribe_groq(media, _profile(), row)

    assert list(scratch.glob("*.flac")) == []


# ── cancellation safety ──────────────────────────────────────────────────────

async def test_cancellation_cleans_temp_file_and_propagates(scratch, fake_extract, tmp_path, monkeypatch):
    """Cancellation while the upload is in flight removes the temp audio.

    Extraction returns a real temp file, then the network call blocks; the
    adapter's ``finally`` must delete the file before ``CancelledError``
    propagates. (Cancellation *during* extraction is covered by the real-ffmpeg
    test, which asserts the extractor cleans its own partial output.)
    """
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"v")
    started = asyncio.Event()

    async def hanging_post(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()  # never returns
        raise AssertionError("unreachable")

    monkeypatch.setattr(groq, "_post_transcription", hanging_post)

    task = asyncio.create_task(
        groq.transcribe_groq(
            media, _profile(), _settings_row(base_url="http://127.0.0.1:1/openai/v1"),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert list(scratch.glob("*.flac")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_cancellation_kills_ffmpeg_subprocess(scratch, tmp_path):
    # A long-running real ffmpeg decode gives the cancel a live child to kill.
    media = tmp_path / "clip.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=600", str(media)],
        check=True,
    )

    task = asyncio.create_task(groq.extract_mono16k_flac(media))
    await asyncio.sleep(0.4)  # let ffmpeg start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert list(scratch.glob("*.flac")) == []


# ── real ffmpeg extraction (local integration) ───────────────────────────────

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_extract_real_mono16k_flac(scratch, tmp_path):
    source = tmp_path / "source.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ar", "44100", "-ac", "2",
         str(source)],
        check=True,
    )

    out = await groq.extract_mono16k_flac(source)
    try:
        assert out.is_file() and out.stat().st_size > 0
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate,channels",
             "-of", "default=nw=1", str(out)],
            capture_output=True, text=True, check=True,
        )
        assert "codec_name=flac" in probe.stdout
        assert "sample_rate=16000" in probe.stdout
        assert "channels=1" in probe.stdout
    finally:
        out.unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_end_to_end_real_flac_to_local_server(scratch, tmp_path):
    """Real ffmpeg FLAC uploaded to a local Groq-shaped server.

    This is a LOCAL integration test: it proves extraction + multipart upload +
    normalization work together. It does NOT prove Groq's live transcription
    (no credential available); that remains unverified here.
    """
    source = tmp_path / "source.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(source)],
        check=True,
    )

    with _FakeGroq([(200, {}, _verbose_json())]) as server:
        row = _settings_row(base_url=server.base_url)
        result = await groq.transcribe_groq(source, _profile(), row, language="en")

        assert len(server.requests) == 1
        body = server.requests[0]["body"]
        assert b"fLaC" in body  # real FLAC magic in the uploaded file part

    assert result.source == "groq"
    assert result.segments[0].source_end_ms == 2000
