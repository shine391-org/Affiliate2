# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for Edge TTS word-metadata sidecar capture.

Transport-only mock: edge_tts.Communicate is replaced with a fake that writes
deterministic audio bytes and JSONL metadata. No real TTS calls.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from backend.services import edge_tts_service


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_word_event(text: str, offset_100ns: int, duration_100ns: int) -> dict:
    """Build a WordBoundary event in edge_tts native format (100ns ticks)."""
    return {
        "type": "WordBoundary",
        "offset": offset_100ns,
        "duration": duration_100ns,
        "text": text,
    }


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_communicate(monkeypatch):
    """Replace edge_tts.Communicate with a controllable fake.

    Returns a ``FakeCommunicateFactory`` whose ``instances`` list is populated
    as each Communicate is constructed.  Tests configure the *next* instance
    via ``factory.next_events = [...]`` BEFORE calling ``generate_voice``.
    """
    instances = []

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            self.text = text
            self.voice = voice
            self.kwargs = kwargs
            self._events: list[dict] = []
            self._raw_metadata: str | None = None
            self._fail: bool = False
            self._audio_data: bytes = b"fake-audio-data"
            instances.append(self)

        async def save(self, audio_fname, metadata_fname=None):
            if self._fail:
                raise RuntimeError("TTS failed")
            Path(audio_fname).write_bytes(self._audio_data)
            if metadata_fname:
                with open(metadata_fname, "w", encoding="utf-8") as f:
                    if self._raw_metadata is not None:
                        f.write(self._raw_metadata)
                    else:
                        for event in self._events:
                            f.write(json.dumps(event) + "\n")

    class FakeCommunicateFactory:
        def __init__(self):
            self.instances = instances
            self.next_events: list[dict] | None = None
            self.next_raw_metadata: str | None = None
            self.next_fail: bool = False
            self.next_audio_data: bytes = b"fake-audio-data"

        def __call__(self, text, voice, **kwargs):
            inst = FakeCommunicate(text, voice, **kwargs)
            if self.next_events is not None:
                inst._events = list(self.next_events)
            if self.next_raw_metadata is not None:
                inst._raw_metadata = self.next_raw_metadata
            inst._fail = self.next_fail
            inst._audio_data = self.next_audio_data
            return inst

    factory = FakeCommunicateFactory()
    monkeypatch.setattr("edge_tts.Communicate", factory)
    return factory


# ── Happy path ───────────────────────────────────────────────────────────────

def test_generate_voice_creates_word_metadata_sidecar(mock_communicate, tmp_path):
    """Successful generation produces a .words.json sidecar with correct format."""
    mock_communicate.next_events = [
        _make_word_event("hello", 0, 5_000_000),
        _make_word_event("world", 5_000_000, 3_000_000),
    ]

    output = tmp_path / "test.wav"
    result = asyncio.run(edge_tts_service.generate_voice(
        "hello world",
        output_path=output,
    ))

    assert result == output
    assert output.exists()

    sidecar = tmp_path / "test.wav.words.json"
    assert sidecar.exists()

    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["text"] == "hello world"
    assert payload["processed_text"] == "hello world"
    assert payload["voice"] == edge_tts_service.DEFAULT_VOICE
    assert payload["timing_basis"] == "provider_word_boundary"
    assert len(payload["audio_sha256"]) == 64

    words = payload["words"]
    assert len(words) == 2
    assert words[0]["word"] == "hello"
    assert words[0]["start_ms"] == 0.0
    assert words[0]["end_ms"] == 500.0
    assert words[1]["word"] == "world"
    assert words[1]["start_ms"] == 500.0
    assert words[1]["end_ms"] == 800.0


def test_generate_voice_audio_sha256_matches(mock_communicate, tmp_path):
    """The audio SHA256 in the sidecar matches the actual audio file."""
    mock_communicate.next_events = [_make_word_event("hello", 0, 5_000_000)]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    sidecar = tmp_path / "test.wav.words.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))

    actual_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    assert payload["audio_sha256"] == actual_sha256


def test_generate_voice_updates_existing_sidecar(mock_communicate, tmp_path):
    """Regeneration overwrites the previous sidecar."""
    output = tmp_path / "test.wav"
    sidecar = tmp_path / "test.wav.words.json"

    mock_communicate.next_events = [_make_word_event("hello", 0, 5_000_000)]
    asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))
    assert sidecar.exists()

    mock_communicate.next_events = [_make_word_event("world", 0, 3_000_000)]
    asyncio.run(edge_tts_service.generate_voice("world", output_path=output))

    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["text"] == "world"
    assert payload["words"][0]["word"] == "world"


# ── 100ns conversion ─────────────────────────────────────────────────────────

def test_generate_voice_converts_100ns_offsets_to_ms(mock_communicate, tmp_path):
    """100ns tick offsets are correctly converted to milliseconds."""
    mock_communicate.next_events = [
        _make_word_event("a", 10_000, 20_000),   # 1 ms start, 2 ms duration
        _make_word_event("b", 30_000, 40_000),   # 3 ms start, 4 ms duration
    ]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("a b", output_path=output))

    sidecar = tmp_path / "test.wav.words.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    words = payload["words"]
    assert words[0]["start_ms"] == 1.0
    assert words[0]["end_ms"] == 3.0
    assert words[1]["start_ms"] == 3.0
    assert words[1]["end_ms"] == 7.0


# ── Empty / corrupt metadata ────────────────────────────────────────────────

def test_generate_voice_empty_metadata_no_sidecar(mock_communicate, tmp_path):
    """Empty metadata produces no sidecar."""
    mock_communicate.next_events = []

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    assert output.exists()
    sidecar = tmp_path / "test.wav.words.json"
    assert not sidecar.exists()


def test_generate_voice_corrupt_metadata_no_sidecar(mock_communicate, tmp_path):
    """Corrupt JSONL lines yield no valid events → no sidecar."""
    mock_communicate.next_raw_metadata = "not json\n{bad json\n"

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    assert output.exists()
    sidecar = tmp_path / "test.wav.words.json"
    assert not sidecar.exists()


def test_generate_voice_partial_corrupt_metadata_uses_valid_lines(mock_communicate, tmp_path):
    """Valid JSONL lines are kept even when other lines are corrupt."""
    mock_communicate.next_raw_metadata = (
        '{"type": "WordBoundary", "offset": 0, "duration": 5000000, "text": "hello"}\n'
        "not json\n"
        '{"type": "WordBoundary", "offset": 5000000, "duration": 3000000, "text": "world"}\n'
    )

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello world", output_path=output))

    sidecar = tmp_path / "test.wav.words.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert len(payload["words"]) == 2


# ── Validation ───────────────────────────────────────────────────────────────

def test_generate_voice_non_monotonic_events_no_sidecar(mock_communicate, tmp_path):
    """Non-monotonic events produce no sidecar."""
    mock_communicate.next_events = [
        _make_word_event("hello", 5_000_000, 3_000_000),
        _make_word_event("world", 0, 3_000_000),   # goes backwards
    ]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello world", output_path=output))

    assert output.exists()
    sidecar = tmp_path / "test.wav.words.json"
    assert not sidecar.exists()


def test_generate_voice_infinite_offset_no_sidecar(mock_communicate, tmp_path):
    """Infinite / NaN offsets produce no sidecar."""
    mock_communicate.next_events = [
        _make_word_event("hello", 0, 5_000_000),
        {"type": "WordBoundary", "offset": float("inf"),
         "duration": 3_000_000, "text": "world"},
    ]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello world", output_path=output))

    assert output.exists()
    sidecar = tmp_path / "test.wav.words.json"
    assert not sidecar.exists()


def test_generate_voice_negative_offset_no_sidecar(mock_communicate, tmp_path):
    """Negative offsets produce no sidecar."""
    mock_communicate.next_events = [
        _make_word_event("hello", -10_000, 5_000_000),
    ]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    assert output.exists()
    sidecar = tmp_path / "test.wav.words.json"
    assert not sidecar.exists()


# ── Failure cleanup ──────────────────────────────────────────────────────────

def test_generate_voice_failure_removes_stale_sidecar(mock_communicate, tmp_path):
    """Failed generation removes any existing sidecar."""
    output = tmp_path / "test.wav"
    sidecar = tmp_path / "test.wav.words.json"

    output.write_bytes(b"old-audio")
    sidecar.write_text(json.dumps({"version": 1, "words": []}), encoding="utf-8")

    mock_communicate.next_fail = True

    with pytest.raises(Exception, match="Edge TTS generation failed"):
        asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    assert not sidecar.exists()


def test_generate_voice_empty_audio_no_sidecar(mock_communicate, tmp_path):
    """Empty audio produces no sidecar."""
    mock_communicate.next_events = [_make_word_event("hello", 0, 5_000_000)]
    mock_communicate.next_audio_data = b""

    output = tmp_path / "test.wav"
    with pytest.raises(Exception, match="empty audio"):
        asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    sidecar = tmp_path / "test.wav.words.json"
    assert not sidecar.exists()


# ── Special text ─────────────────────────────────────────────────────────────

def test_generate_voice_special_text_preserved(mock_communicate, tmp_path):
    """Vietnamese diacritics and punctuation are preserved in sidecar."""
    text = "Xin chào! Bạn khỏe không? Tốt lắm..."
    mock_communicate.next_events = [
        _make_word_event("Xin", 0, 3_000_000),
        _make_word_event("chào!", 3_000_000, 4_000_000),
    ]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice(text, output_path=output))

    sidecar = tmp_path / "test.wav.words.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["text"] == text
    # _add_normal_pauses normalises trailing ellipsis to "... "
    assert payload["processed_text"] == "Xin chào! Bạn khỏe không? Tốt lắm... "


def test_generate_voice_uses_word_boundary(mock_communicate, tmp_path):
    """Communicate is constructed with boundary='WordBoundary'."""
    mock_communicate.next_events = [_make_word_event("hello", 0, 5_000_000)]

    output = tmp_path / "test.wav"
    asyncio.run(edge_tts_service.generate_voice("hello", output_path=output))

    assert mock_communicate.instances[0].kwargs.get("boundary") == "WordBoundary"
