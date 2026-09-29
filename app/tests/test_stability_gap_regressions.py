# SPDX-License-Identifier: AGPL-3.0-only
"""Regression tests covering stability gaps:
- Library source ownership (preservation on success, failure, review pause, direct upload cleaned)
- Datetime repair (types, malformed non-null fail-closed, backup requirement, rollback, idempotency)
- Gemini resilience & Checkpoint rerun (503/429/400, fallback model, checkpoint preserved on rerun, invalidated on config change)
- Whisper device selection and fallback (CUDA detection, CPU fallback)
- NVENC detection and fallback to libx264
- Mask bbox validation and parsing (x=0, full width, invalid geometries, solid/blur)
- Source self-repair (id + hash verification, no guess/heuristic copy, no overwrite)
- GPU concurrency gate (gate=1, heavy section serialization, error release, no deadlock)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.core.ai_provider import AIClient, AIProvider
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.migrations.repair_datetime_types import (
    MalformedDatetimeError,
    repair_database_datetimes,
    to_canonical_datetime_str,
)
from backend.services import caption_service as cs
from backend.services import ffmpeg_service as fs
from backend.services import localization_service as ls
from backend.services import localization_stage_service as lss
from backend.services import whisper_service as ws


# ==============================================================================
# A. Library source ownership
# ==============================================================================
@pytest.mark.asyncio
async def test_library_source_preserved_on_failure(tmp_path, monkeypatch):
    """When downloaded_video_id is provided, the library source file must NOT be deleted on error."""
    source_file = tmp_path / "original_lib_video.mp4"
    source_file.write_bytes(b"content-library-source")

    from backend.core import tool_runners as tr

    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: tmp_path / "storage"))
    monkeypatch.setattr(tr, "_tool_fail", AsyncMock())
    monkeypatch.setattr(tr, "_tool_progress", AsyncMock())
    cleanup_calls = []
    monkeypatch.setattr(
        tr, "_release_localization_torch_cache_sync",
        lambda: cleanup_calls.append("released"),
    )
    monkeypatch.setattr(tr, "_load_user_settings", AsyncMock(return_value=SimpleNamespace(
        localization_profiles_json=None, ai_provider="compatible", whisper_quality="balanced",
    )))

    mock_profile = LocalizationProfile(id="p1", name="Profile 1", audio_mode="fast_duck")
    monkeypatch.setattr(ls, "materialize_localization_profile", lambda *a, **k: mock_profile)

    async def fail_transcribe(*a, **k):
        raise RuntimeError("ASR pipeline simulated crash")

    monkeypatch.setattr(ls, "transcribe_for_localization", fail_transcribe)

    await tr.run_tool_translate(
        job_id="job-lib-fail",
        in_path=source_file,
        target_language="Vietnamese",
        mode="full_dub",
        voice_id="",
        caption_style="viral",
        emoji_style="moderate",
        cleanup_input=False,
        downloaded_video_id="video-lib-123",
    )

    tr._tool_fail.assert_awaited_once()
    assert cleanup_calls == ["released"], "GPU cache cleanup must run even when ASR fails"
    assert source_file.exists(), "Library source was deleted on failure!"
    assert source_file.read_bytes() == b"content-library-source"


@pytest.mark.asyncio
async def test_library_source_preserved_on_success_and_review_pause(tmp_path, monkeypatch):
    """Library source must remain intact on success and review pause."""
    source_file = tmp_path / "lib_source2.mp4"
    source_file.write_bytes(b"content-library-source-2")

    from backend.core import tool_runners as tr

    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: tmp_path / "storage"))
    monkeypatch.setattr(tr, "_tool_progress", AsyncMock())
    monkeypatch.setattr(tr, "_tool_success", AsyncMock())
    monkeypatch.setattr(tr, "_tool_fail", AsyncMock())
    monkeypatch.setattr(tr, "_load_user_settings", AsyncMock(return_value=SimpleNamespace(
        localization_profiles_json=None, ai_provider="compatible", whisper_quality="balanced",
    )))

    mock_profile = LocalizationProfile(id="p1", name="Profile 1", audio_mode="fast_duck")
    monkeypatch.setattr(ls, "materialize_localization_profile", lambda *a, **k: mock_profile)

    async def stub_transcribe(*a, **k):
        return LocalizationTranscript(
            language="en",
            segments=[LocalizationSegment(id="s1", source_text="Hello", source_start_ms=0, source_end_ms=1000)],
        )

    async def stub_translate(*a, **k):
        return LocalizationTranscript(
            language="vi",
            segments=[LocalizationSegment(id="s1", source_text="Hello", translated_text="Xin chào", source_start_ms=0, source_end_ms=1000)],
        )

    async def stub_prepare(t, **k):
        return t

    monkeypatch.setattr(ls, "transcribe_for_localization", stub_transcribe)
    monkeypatch.setattr(ls, "prepare_semantic_transcript", stub_prepare)
    monkeypatch.setattr(ls, "translate_transcript", stub_translate)

    # With review_translation=True, should pause after C6 and preserve library source
    await tr.run_tool_translate(
        job_id="job-lib-pause",
        in_path=source_file,
        target_language="Vietnamese",
        mode="full_dub",
        voice_id="",
        caption_style="viral",
        emoji_style="moderate",
        cleanup_input=False,
        downloaded_video_id="video-lib-pause",
        review_translation=True,
    )

    tr._tool_success.assert_awaited_once()
    assert source_file.exists(), "Library source was deleted on review pause!"
    assert source_file.read_bytes() == b"content-library-source-2"


@pytest.mark.asyncio
async def test_direct_upload_cleaned_when_cleanup_input_is_true(tmp_path):
    """Direct uploads with cleanup_input=True must be cleaned up."""
    ephemeral_upload = tmp_path / "ephemeral_upload.mp4"
    ephemeral_upload.write_bytes(b"ephemeral-bytes")

    from backend.core import tool_runners as tr

    cleanup = [ephemeral_upload]
    tr._cleanup_paths(*cleanup)
    assert not ephemeral_upload.exists(), "Direct upload should have been cleaned up!"


# ==============================================================================
# B. Datetime repair
# ==============================================================================
def test_datetime_repair_types_and_safety(tmp_path):
    db_file = tmp_path / "test_datetimes.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            created_at DATETIME,
            updated_at DATETIME NOT NULL,
            published_date TIMESTAMP
        )
    """)
    # Row 1: Valid integer epoch, valid ISO text, valid float epoch
    cur.execute("INSERT INTO items VALUES ('1', 1700000000, '2025-01-01 12:00:00', 1700000000.5)")
    # Row 2: Already canonical format
    cur.execute("INSERT INTO items VALUES ('2', '2025-01-01 12:00:00.000000', '2025-01-01 12:00:00.000000', '2025-01-01 12:00:00.000000')")
    # Row 3: Malformed NULLABLE datetime — the data-loss case. Repair must now
    # fail closed and preserve the raw value instead of writing NULL over it.
    cur.execute("INSERT INTO items VALUES ('3', 'not-a-datetime-text', '2025-01-01 12:00:00.000000', NULL)")
    conn.commit()
    conn.close()

    # A malformed value anywhere aborts the whole repair: nothing is written.
    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db_file, backup=True)

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    # Row 1 parseable-but-non-canonical must ALSO be untouched — the abort
    # happens during scan, so there is no partial repair.
    cur.execute("SELECT created_at, updated_at, published_date FROM items WHERE id='1'")
    r1 = cur.fetchone()
    assert r1 == (1700000000, "2025-01-01 12:00:00", 1700000000.5), "Partial repair before the abort!"

    cur.execute("SELECT created_at FROM items WHERE id='3'")
    r3 = cur.fetchone()
    assert r3[0] == "not-a-datetime-text", "Malformed nullable datetime must be preserved, not NULLed"
    conn.close()

    # No backup is created for an aborted repair — nothing was mutated.
    assert not list(tmp_path.glob(f"{db_file.name}.backup_before_repair_*"))

    # Drop the malformed row; the remaining repairable rows then normalize.
    conn = sqlite3.connect(str(db_file))
    conn.execute("DELETE FROM items WHERE id='3'")
    conn.commit()
    conn.close()

    res = repair_database_datetimes(db_file, backup=True)
    assert res["repaired_rows"] > 0

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT created_at, updated_at, published_date FROM items WHERE id='1'")
    r1 = cur.fetchone()
    assert r1[0] is not None and "." in r1[0]
    assert r1[1] is not None and "." in r1[1]
    assert r1[2] is not None and "." in r1[2]

    # Second run must be completely idempotent (0 repaired rows)
    res2 = repair_database_datetimes(db_file, backup=True)
    assert res2["repaired_rows"] == 0
    conn.close()


def test_datetime_repair_backup_is_wal_safe_sqlite_snapshot(tmp_path):
    db_file = tmp_path / "test_wal_snapshot.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE items (id TEXT PRIMARY KEY, created_at DATETIME)")
    conn.execute("INSERT INTO items VALUES ('wal-row', 1700000000)")
    conn.commit()

    assert db_file.with_name(f"{db_file.name}-wal").exists()

    result = repair_database_datetimes(db_file, backup=True)
    assert result["repaired_rows"] == 1

    backups = list(tmp_path.glob(f"{db_file.name}.backup_before_repair_*") )
    assert len(backups) == 1
    backup_conn = sqlite3.connect(str(backups[0]))
    try:
        row = backup_conn.execute(
            "SELECT id, created_at, typeof(created_at) FROM items WHERE id='wal-row'"
        ).fetchone()
    finally:
        backup_conn.close()
        conn.close()

    assert row == ("wal-row", 1700000000, "integer")


def test_datetime_repair_malformed_not_null_fails_closed(tmp_path):
    db_file = tmp_path / "test_not_null_fail.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE critical_items (
            id TEXT PRIMARY KEY,
            updated_at DATETIME NOT NULL
        )
    """)
    cur.execute("INSERT INTO critical_items VALUES ('bad-1', 'garbage-corrupt-date')")
    conn.commit()
    conn.close()

    # NOT NULL and nullable now fail identically — nullable-ness no longer
    # decides whether a value is destroyed, so the message no longer says
    # "non-null" either.
    with pytest.raises(MalformedDatetimeError) as excinfo:
        repair_database_datetimes(db_file, backup=True)

    message = str(excinfo.value)
    assert "critical_items" in message
    assert "updated_at" in message
    assert "garbage-corrupt-date" in message

    # And the value must be preserved, not merely reported.
    conn = sqlite3.connect(str(db_file))
    try:
        assert conn.execute("SELECT updated_at FROM critical_items").fetchone() == (
            "garbage-corrupt-date",
        )
    finally:
        conn.close()


def test_datetime_repair_backup_failure_aborts(tmp_path, monkeypatch):
    db_file = tmp_path / "test_backup_fail.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("CREATE TABLE tbl (id TEXT PRIMARY KEY, created_at DATETIME)")
    cur.execute("INSERT INTO tbl VALUES ('1', 1700000000)")
    conn.commit()
    conn.close()

    from backend.migrations import repair_datetime_types as repair_module

    def fail_backup(*a, **k):
        raise OSError("Disk full simulation on backup")

    monkeypatch.setattr(repair_module, "_backup_sqlite_database", fail_backup)

    with pytest.raises(RuntimeError, match="Database backup failed before datetime repair"):
        repair_database_datetimes(db_file, backup=True)

    # Database must remain unchanged
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT created_at, typeof(created_at) FROM tbl WHERE id='1'")
    row = cur.fetchone()
    assert row[1] == "integer", "Database was modified despite backup failure!"
    conn.close()


# ==============================================================================
# C. Gemini resilience & Translation Checkpoint
# ==============================================================================
class _MockTransientAI:
    def __init__(self, succeed_on_attempt: int = 1):
        self.attempts = 0
        self.succeed_on_attempt = succeed_on_attempt
        self.provider = AIProvider.COMPATIBLE
        self.model = "gemini-2.5-flash"

    async def chat(self, messages, **kwargs):
        self.attempts += 1
        if self.attempts < self.succeed_on_attempt:
            raise RuntimeError("503 Service Unavailable: Model is overloaded")
        return json.dumps([
            {"id": "seg-1", "text": "Đoạn một"},
            {"id": "seg-2", "text": "Đoạn hai"},
        ], ensure_ascii=False)


@pytest.mark.asyncio
async def test_gemini_503_retry_and_resilience(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    ai = _MockTransientAI(succeed_on_attempt=3)
    resp = await ls._call_ai_chat_resilient(
        ai, fallback_ai=None, messages=[{"role": "user", "content": "hi"}], max_attempts=3
    )
    assert "Đoạn một" in resp
    assert ai.attempts == 3


@pytest.mark.asyncio
async def test_fallback_ai_called_when_primary_exhausted(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    primary_ai = _MockTransientAI(succeed_on_attempt=999)
    fallback_ai = AIClient(
        provider=AIProvider.COMPATIBLE,
        api_key="test-key",
        model="gemini-2.5-flash-fallback",
        base_url="https://test.local/v1",
        max_retries=0,
    )

    async def fb_chat(*a, **k):
        return json.dumps([{"id": "seg-1", "text": "Bản dịch dự phòng"}], ensure_ascii=False)

    fallback_ai.chat = fb_chat

    resp = await ls._call_ai_chat_resilient(
        primary_ai, fallback_ai=fallback_ai, messages=[{"role": "user", "content": "hi"}], max_attempts=2
    )
    assert "Bản dịch dự phòng" in resp
    assert isinstance(fallback_ai.provider, AIProvider)


@pytest.mark.asyncio
async def test_gemini_429_retry_and_400_no_retry(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # 1. 429 status code retries
    class Mock429AI:
        def __init__(self):
            self.attempts = 0
            self.provider = AIProvider.COMPATIBLE
            self.model = "gemini-2.5-flash"
        async def chat(self, messages, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                err = RuntimeError("429 Too Many Requests: Rate limit exceeded")
                err.status_code = 429
                raise err
            return json.dumps([{"id": "s1", "text": "Đoạn sau retry"}], ensure_ascii=False)

    ai_429 = Mock429AI()
    resp = await ls._call_ai_chat_resilient(ai_429, fallback_ai=None, messages=[{"role": "user", "content": "hi"}], max_attempts=2)
    assert "Đoạn sau retry" in resp
    assert ai_429.attempts == 2

    # 2. 400 status code fails immediately without retry
    class Mock400AI:
        def __init__(self):
            self.attempts = 0
            self.provider = AIProvider.COMPATIBLE
            self.model = "gemini-2.5-flash"
        async def chat(self, messages, **kwargs):
            self.attempts += 1
            err = RuntimeError("400 Bad Request: Invalid prompt payload")
            err.status_code = 400
            raise err

    ai_400 = Mock400AI()
    with pytest.raises(RuntimeError, match="400 Bad Request"):
        await ls._call_ai_chat_resilient(ai_400, fallback_ai=None, messages=[{"role": "user", "content": "hi"}], max_attempts=3)
    assert ai_400.attempts == 1, "Non-transient 400 error should fail immediately without retry!"


@pytest.mark.asyncio
async def test_fallback_provider_missing_credentials_fails_closed(tmp_path):
    transcript = LocalizationTranscript(
        language="en",
        segments=[LocalizationSegment(id="s1", source_text="Hello", source_start_ms=0, source_end_ms=1000)],
    )
    profile = LocalizationProfile(
        id="p-fb-bad",
        name="Bad Fallback Profile",
        translation_fallback_model="claude-sonnet-4-6",
        translation_fallback_provider="anthropic",
    )
    mock_ai = SimpleNamespace(provider=AIProvider.COMPATIBLE, model="gemini-2.5-flash", api_key="dummy", base_url=None)
    with patch.object(ls, "_profile_ai_client", return_value=mock_ai):
        with pytest.raises(ValueError, match="Invalid fallback AI provider configuration"):
            await ls.translate_transcript(
                transcript,
                target_language="vi",
                profile=profile,
                user_settings=SimpleNamespace(ai_provider="compatible"),
                for_dub=False,
            )


@pytest.mark.asyncio
async def test_checkpoint_invalidated_on_config_change(tmp_path, monkeypatch):
    trans_dir = tmp_path / "translation"
    trans_dir.mkdir(parents=True)
    segments = [LocalizationSegment(id="s0", source_text="Sentence 0", source_start_ms=0, source_end_ms=1000)]
    transcript = LocalizationTranscript(text="Sentence 0", language="en", segments=segments)
    profile = LocalizationProfile(id="p1", name="Profile 1", translation_preset="natural_vietnamese")

    checkpoint_file = trans_dir / "checkpoint.json"
    checkpoint_data = {
        "semantic_hash": "different-hash",
        "target_language": "vi",
        "provider": "compatible",
        "model": "gemini-2.5-flash",
        "preset": "natural_vietnamese",
        "instructions": "",
        "guardrails": profile.translation_guardrails.strip(),
        "glossary_hash": ls.hashlib.sha256(b"{}").hexdigest()[:16],
        "batches": {
            "0": {
                "texts": ["Stale translation"],
                "failed": [],
            }
        }
    }
    checkpoint_file.write_text(json.dumps(checkpoint_data, ensure_ascii=False), encoding="utf-8")

    called_batches = []
    async def mock_batch_translate(ai, batch, **kwargs):
        called_batches.append(batch[0].id)
        return ["Fresh translation"], set()

    with patch.object(ls, "_translate_batch_resilient", side_effect=mock_batch_translate):
        mock_ai = SimpleNamespace(provider=AIProvider.COMPATIBLE, model="gemini-2.5-flash")
        with patch.object(ls, "_profile_ai_client", return_value=mock_ai):
            out = await ls.translate_transcript(
                transcript,
                target_language="vi",
                profile=profile,
                user_settings=SimpleNamespace(ai_provider="compatible"),
                for_dub=False,
                checkpoint_dir=trans_dir,
            )

    assert "s0" in called_batches, "Incompatible checkpoint must be discarded and retranslated!"
    assert out.segments[0].translated_text == "Fresh translation"


@pytest.mark.asyncio
async def test_checkpoint_records_only_successful_batches_and_retries_failed_batch(tmp_path, monkeypatch):
    trans_dir = tmp_path / "translation"
    transcript = LocalizationTranscript(
        text="Sentence 0 Sentence 1",
        language="en",
        segments=[
            LocalizationSegment(id="s0", source_text="Sentence 0", source_start_ms=0, source_end_ms=1000),
            LocalizationSegment(id="s1", source_text="Sentence 1", source_start_ms=1000, source_end_ms=2000),
        ],
    )
    profile = LocalizationProfile(id="p1", name="Profile 1", qc_profile="standard")
    mock_ai = SimpleNamespace(
        provider=AIProvider.COMPATIBLE,
        model="gemini-2.5-flash",
        base_url="https://primary.example/v1",
    )
    monkeypatch.setattr(ls, "_TRANSLATION_BATCH_SIZE", 1)
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *args: mock_ai)

    first_calls = []

    async def first_attempt(ai, batch, **kwargs):
        first_calls.append(batch[0].id)
        if batch[0].id == "s1":
            return [batch[0].source_text], {0}
        return ["Dịch 0"], set()

    monkeypatch.setattr(ls, "_translate_batch_resilient", first_attempt)
    await ls.translate_transcript(
        transcript,
        target_language="vi",
        profile=profile,
        user_settings=SimpleNamespace(ai_provider="compatible"),
        for_dub=False,
        checkpoint_dir=trans_dir,
    )

    checkpoint = json.loads((trans_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert first_calls == ["s0", "s1"]
    assert set(checkpoint["batches"]) == {"0"}

    retry_calls = []

    async def retry_attempt(ai, batch, **kwargs):
        retry_calls.append(batch[0].id)
        return ["Dịch 1"], set()

    monkeypatch.setattr(ls, "_translate_batch_resilient", retry_attempt)
    retried = await ls.translate_transcript(
        transcript,
        target_language="vi",
        profile=profile,
        user_settings=SimpleNamespace(ai_provider="compatible"),
        for_dub=False,
        checkpoint_dir=trans_dir,
    )

    assert retry_calls == ["s1"]
    assert retried.segments[0].translated_text == "Dịch 0"
    assert retried.segments[1].translated_text == "Dịch 1"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_config", ["primary_base_url", "fallback_model"])
async def test_checkpoint_identity_binds_endpoint_and_fallback_config(tmp_path, monkeypatch, changed_config):
    trans_dir = tmp_path / "translation"
    transcript = LocalizationTranscript(
        text="Sentence 0",
        language="en",
        segments=[LocalizationSegment(id="s0", source_text="Sentence 0", source_start_ms=0, source_end_ms=1000)],
    )
    profile = LocalizationProfile(
        id="p1",
        name="Profile 1",
        qc_profile="standard",
        translation_fallback_provider="lmstudio",
        translation_fallback_model="fallback-a",
    )
    primary = SimpleNamespace(
        provider=AIProvider.COMPATIBLE,
        model="primary-model",
        base_url="https://primary-a.example/v1",
    )
    monkeypatch.setattr(ls, "_profile_ai_client", lambda *args: primary)

    calls = []

    async def translate(ai, batch, **kwargs):
        calls.append(batch[0].id)
        return [f"Dịch {len(calls)}"], set()

    monkeypatch.setattr(ls, "_translate_batch_resilient", translate)
    await ls.translate_transcript(
        transcript,
        target_language="vi",
        profile=profile,
        user_settings=SimpleNamespace(ai_provider="compatible"),
        for_dub=False,
        checkpoint_dir=trans_dir,
    )
    assert calls == ["s0"]

    if changed_config == "primary_base_url":
        primary.base_url = "https://primary-b.example/v1"
    else:
        profile = profile.model_copy(update={"translation_fallback_model": "fallback-b"})

    await ls.translate_transcript(
        transcript,
        target_language="vi",
        profile=profile,
        user_settings=SimpleNamespace(ai_provider="compatible"),
        for_dub=False,
        checkpoint_dir=trans_dir,
    )

    assert calls == ["s0", "s0"]


@pytest.mark.asyncio
async def test_rerun_preserves_translation_checkpoint_and_skips_finished_batches(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: storage_root))

    job_dir = storage_root / "localization_jobs" / "job-resilience"
    trans_dir = job_dir / "translation"
    trans_dir.mkdir(parents=True)

    # 26 segments with _TRANSLATION_BATCH_SIZE=12 gives:
    # Batch 0: indices 0..11 (12 items)
    # Batch 1: indices 12..23 (12 items)
    # Batch 2: indices 24..25 (2 items)
    segments = [
        LocalizationSegment(id=f"s{i}", source_text=f"Sentence {i}", source_start_ms=i*1000, source_end_ms=(i+1)*1000)
        for i in range(26)
    ]
    transcript = LocalizationTranscript(text="All sentences", language="en", segments=segments)
    profile = LocalizationProfile(id="p1", name="Profile 1", translation_preset="natural_vietnamese")

    # Seed partial checkpoint where batch 0 is completed
    semantic_sig = ls.hashlib.sha256(
        "".join(f"{s.id}:{s.source_text}" for s in transcript.segments).encode("utf-8")
    ).hexdigest()[:16]
    checkpoint_file = trans_dir / "checkpoint.json"
    checkpoint_data = {
        "semantic_hash": semantic_sig,
        "target_language": "vi",
        "provider": "compatible",
        "model": "gemini-2.5-flash",
        "base_url": "",
        "preset": "natural_vietnamese",
        "instructions": "",
        "guardrails": profile.translation_guardrails.strip(),
        "glossary_hash": ls.hashlib.sha256(b"{}").hexdigest()[:16],
        "fallback_provider": "",
        "fallback_model": "",
        "fallback_base_url": "",
        "batches": {
            "0": {
                "texts": [f"Dịch {i}" for i in range(12)],
                "failed": [],
            }
        }
    }
    checkpoint_file.write_text(json.dumps(checkpoint_data, ensure_ascii=False), encoding="utf-8")
    final_tr = trans_dir / "transcript.json"
    final_tr.write_text("{}", encoding="utf-8")

    run = lss.LocalizationRun("job-resilience", source_path=str(tmp_path / "src.mp4"))
    run.invalidate_from(
        "translation",
        delete_artifacts=True,
        preserve_files={"checkpoint.json"},
    )

    # Verification: final transcript.json deleted, but checkpoint.json preserved!
    assert not final_tr.exists(), "Final transcript.json should have been deleted!"
    assert checkpoint_file.exists(), "translation/checkpoint.json must be preserved!"

    # Now run translate_transcript: batch 0 must NOT call AI, batches 1 and 2 should call AI
    called_batches = []
    async def mock_batch_translate(ai, batch, **kwargs):
        called_batches.append(batch[0].id)
        return [f"Dịch {s.id}" for s in batch], set()

    with patch.object(ls, "_translate_batch_resilient", side_effect=mock_batch_translate):
        mock_ai = SimpleNamespace(provider=AIProvider.COMPATIBLE, model="gemini-2.5-flash")
        with patch.object(ls, "_profile_ai_client", return_value=mock_ai):
            out = await ls.translate_transcript(
                transcript,
                target_language="vi",
                profile=profile,
                user_settings=SimpleNamespace(ai_provider="compatible"),
                for_dub=False,
                checkpoint_dir=trans_dir,
            )

    assert "s0" not in called_batches, "Batch 0 was in checkpoint and should not be translated again!"
    assert "s12" in called_batches, "Batch 1 should have been translated"
    assert "s24" in called_batches, "Batch 2 should have been translated"
    assert out.segments[0].translated_text == "Dịch 0"
    assert out.segments[12].translated_text == "Dịch s12"


# ==============================================================================
# D. Whisper CUDA and CPU fallback
# ==============================================================================
def test_whisper_cuda_selection_and_cpu_fallback(monkeypatch):
    import sys
    import types

    fake_ctranslate2 = types.ModuleType("ctranslate2")
    fake_ctranslate2.get_cuda_device_count = lambda: 1
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ctranslate2)

    dev, compute = ws.WhisperService._select_device()
    assert dev == "cuda"
    assert compute == "float16"

    # Test fallback when CUDA init fails
    class MockWhisperModel:
        def __init__(self, model_name, device="cpu", compute_type="int8"):
            if device == "cuda":
                raise RuntimeError("CUDA out of memory simulation")
            self.device = device
            self.compute_type = compute_type

    fake_faster_whisper = types.ModuleType("faster_whisper")
    fake_faster_whisper.WhisperModel = MockWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)

    with ws.WhisperService._lock:
        ws.WhisperService._model = None
        ws.WhisperService._loaded_model_name = None

    loaded = ws.WhisperService.load(quality="fast")
    assert loaded.device == "cpu"
    assert loaded.compute_type == "int8"
    assert ws.WhisperService._loaded_device == "cpu"


# ==============================================================================
# E. NVENC runtime support and fallback
# ==============================================================================
def test_nvenc_detection_and_args(monkeypatch):
    fs._has_nvenc = True
    args = fs.video_encode_args("balanced", prefer_nvenc=True)
    assert "-c:v" in args
    assert "h264_nvenc" in args

    fs._has_nvenc = False
    args_cpu = fs.video_encode_args("balanced", prefer_nvenc=True)
    assert "-c:v" in args_cpu
    assert "libx264" in args_cpu


@pytest.mark.asyncio
async def test_burn_captions_nvenc_failure_falls_back_to_libx264(tmp_path, monkeypatch):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"dummy-mp4")
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]\nTitle: Test\n", encoding="utf-8")
    out = tmp_path / "out.mp4"

    monkeypatch.setattr(cs, "_ffmpeg_has_ass", True)
    monkeypatch.setattr(fs, "has_nvenc_support", lambda: True)
    monkeypatch.setattr(fs, "_probe_dimensions", AsyncMock(return_value=(1080, 1920)))

    calls = []
    class FakeProc:
        def __init__(self, cmd):
            self.cmd = cmd
            if "h264_nvenc" in cmd:
                self.returncode = 1
                self.stderr = "CUDA out of memory / NVENC device init error"
            else:
                self.returncode = 0
                self.stderr = ""
                out.write_bytes(b"encoded-mp4")

    def fake_subprocess_run(cmd, *a, **k):
        calls.append(cmd)
        return FakeProc(cmd)

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    res = await cs.burn_captions(video, ass, output_path=out)
    assert res == out
    encode_calls = [c for c in calls if len(c) > 0 and c[0] == "ffmpeg" and ("h264_nvenc" in c or "libx264" in c)]
    nvenc_attempts = [c for c in encode_calls if "h264_nvenc" in c]
    cpu_attempts = [c for c in encode_calls if "libx264" in c]
    assert len(nvenc_attempts) >= 1, "Expected at least one NVENC attempt"
    assert len(cpu_attempts) >= 1, "Expected at least one libx264 fallback attempt"


# ==============================================================================
# F. Mask validation and parsing
# ==============================================================================
def test_mask_box_normalized_and_validation():
    # 1. x=0, full width preset
    res = cs.parse_and_validate_mask_box(
        {"x": 0.0, "y": 0.72, "width": 1.0, "height": 0.22, "mode": "solid"},
        out_w=1080,
        out_h=1920,
    )
    assert res is not None
    px_x, px_y, px_w, px_h, mode = res
    assert px_x == 0
    assert px_w == 1080
    assert px_h > 0
    assert mode == "solid"

    # 2. Invalid geometries must be rejected
    assert cs.parse_and_validate_mask_box({"x": -0.1, "y": 0.5, "width": 0.5, "height": 0.1}, 1080, 1920) is None
    assert cs.parse_and_validate_mask_box({"x": 0.5, "y": 0.5, "width": 0.0, "height": 0.1}, 1080, 1920) is None
    assert cs.parse_and_validate_mask_box({"x": 0.8, "y": 0.5, "width": 0.5, "height": 0.1}, 1080, 1920) is None  # x+w > 1
    assert cs.parse_and_validate_mask_box({"x": 0.1, "y": 0.9, "width": 0.5, "height": 0.3}, 1080, 1920) is None  # y+h > 1

    # 3. Explicit normalized flag and width > 1.0 out of bounds check
    assert cs.parse_and_validate_mask_box({"x": 0.2, "y": 0.5, "width": 1.2, "height": 0.1, "normalized": True}, 1080, 1920) is None
    assert cs.parse_and_validate_mask_box({"x": 0.2, "y": 0.5, "width": 1.2, "height": 0.1}, 1080, 1920) is None  # normalized range rejects w > 1

    # 4. Blur mode
    blur_res = cs.parse_and_validate_mask_box(
        {"x": 0.05, "y": 0.8, "width": 0.9, "height": 0.1, "mode": "blur"},
        out_w=1080,
        out_h=1920,
    )
    assert blur_res is not None
    assert blur_res[4] == "blur"


# ==============================================================================
# G. Self repair
# ==============================================================================
def test_self_repair_downloaded_from_snapshot(tmp_path, monkeypatch):
    from backend.api import downloaded as dl

    storage_root = tmp_path / "storage"
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: storage_root))

    job_dir = storage_root / "localization_jobs" / "job-snap-1"
    job_dir.mkdir(parents=True)

    snapshot_file = job_dir / "source.mp4"
    snapshot_file.write_bytes(b"verified-snapshot-bytes")
    expected_hash = dl._compute_sha256(snapshot_file)

    manifest_file = job_dir / "manifest.json"
    manifest_data = {
        "request": {"downloaded_video_id": "vid-456"},
        "source_identity": {"sha256": expected_hash, "size_bytes": snapshot_file.stat().st_size},
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    target_path = tmp_path / "downloads" / "vid-456.mp4"

    # 1. Matching ID and hash -> successfully repairs
    assert not target_path.exists()
    ok = dl._repair_missing_downloaded_from_snapshot("vid-456", target_path, expected_sha256=expected_hash)
    assert ok is True
    assert target_path.exists()
    assert target_path.read_bytes() == b"verified-snapshot-bytes"

    # 2. Existing file -> never overwrites
    ok_again = dl._repair_missing_downloaded_from_snapshot("vid-456", target_path, expected_sha256=expected_hash)
    assert ok_again is False

    # 3. Different video ID -> no repair
    target_path.unlink()
    no_match = dl._repair_missing_downloaded_from_snapshot("vid-999", target_path, expected_sha256=expected_hash)
    assert no_match is False
    assert not target_path.exists()

    # 4. Hash mismatch -> no repair
    bad_hash = dl._repair_missing_downloaded_from_snapshot("vid-456", target_path, expected_sha256="wrong-hash")
    assert bad_hash is False
    assert not target_path.exists()


# ==============================================================================
# H. GPU concurrency gate
# ==============================================================================
@pytest.mark.asyncio
async def test_localization_gpu_gate_concurrency():
    from backend.core.tool_runners import localization_gpu_gate

    active = 0
    max_active = 0

    async def heavy_section():
        nonlocal active, max_active
        async with localization_gpu_gate():
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1

    # Launch two concurrent heavy sections
    await asyncio.gather(heavy_section(), heavy_section())
    assert max_active == 1, f"Heavy sections overlapped! Max active was {max_active}"
    assert active == 0


@pytest.mark.asyncio
async def test_localization_gpu_gate_releases_on_exception():
    from backend.core.tool_runners import localization_gpu_gate

    with pytest.raises(ZeroDivisionError):
        async with localization_gpu_gate():
            _ = 1 / 0

    # Ensure gate was released and can immediately be acquired again without deadlock
    acquired = False
    async with localization_gpu_gate():
        acquired = True
    assert acquired is True
