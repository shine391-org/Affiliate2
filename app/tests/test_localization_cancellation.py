# SPDX-License-Identifier: AGPL-3.0-only
"""Real service orchestration; controlled provider I/O, not live-provider QA."""
import asyncio
import json

import pytest

from backend.core.exceptions import JobCancelledError
from backend.services import localization_service as ls
from test_translate_batch_resilience import (
    _EchoAI, _PartialTranslationAI, _pressure_transcript, _profile, _transcript,
)


async def _translate(monkeypatch, ai, **kwargs):
    monkeypatch.setattr(ls, "_profile_ai_route_clients_compat", lambda *_: [ai])
    return await ls.translate_transcript(_transcript(), target_language="vi",
        profile=_profile(), user_settings=object(), for_dub=False, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_pre_cancel_prevents_first_dispatch(monkeypatch, asynchronous):
    ai = _EchoAI()
    async def cancelled():
        await asyncio.sleep(0)
        return True
    with pytest.raises(JobCancelledError):
        await _translate(monkeypatch, ai, cancel_check=cancelled if asynchronous else lambda: True)
    assert ai.calls == 0


@pytest.mark.asyncio
async def test_cancel_in_flight_stops_request_and_no_retry_or_fallback():
    entered, cleaned, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Hanging:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
    ai, fallback = Hanging(), Hanging()
    task = asyncio.create_task(ls._call_ai_chat_resilient(ai, fallback,
        messages=[], cancel_check=cancelled.is_set))
    await asyncio.wait_for(entered.wait(), 2)
    cancelled.set()
    with pytest.raises(JobCancelledError):
        await asyncio.wait_for(task, 2)
    assert ai.calls == 1
    assert fallback.calls == 0
    assert cleaned.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["attempt", "retry_wait", "switch_fallback"])
async def test_cancel_at_dispatch_backoff_and_fallback(boundary):
    cancelled = asyncio.Event()
    events = []
    class Failing:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            exc = RuntimeError("temporarily unavailable" if boundary == "retry_wait" else "bad model")
            exc.status_code = 503 if boundary == "retry_wait" else 400
            raise exc
    ai, fallback = Failing(), Failing()
    async def progress(event):
        events.append(event["event"])
        if event["event"] == boundary:
            cancelled.set()
    async def check():
        # DB-backed checks yield: check BEFORE scheduling provider I/O.
        await asyncio.sleep(0)
        return cancelled.is_set()
    with pytest.raises(JobCancelledError):
        await ls._call_ai_chat_resilient(ai, fallback, messages=[], max_attempts=3,
            progress_cb=progress, cancel_check=check)
    assert boundary in events
    assert ai.calls == (0 if boundary == "attempt" else 1)
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_provider_cancel_is_not_retried_or_reported_as_error():
    events = []
    class Cancelling:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            raise JobCancelledError("cancelled")
    ai, fallback = Cancelling(), Cancelling()
    async def progress(event):
        events.append(event["event"])
    with pytest.raises(JobCancelledError):
        await ls._call_ai_chat_resilient(ai, fallback, messages=[], progress_cb=progress)
    assert ai.calls == 1
    assert fallback.calls == 0
    assert events == ["attempt"]


@pytest.mark.asyncio
async def test_cancel_before_recursive_shape_split():
    cancelled = asyncio.Event()
    class Malformed:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            return "not JSON"
    ai = Malformed()
    async def progress(event):
        if event["event"] == "shape_split":
            cancelled.set()
    with pytest.raises(JobCancelledError):
        await ls._translate_batch_resilient(ai, _transcript().segments,
            target_language="vi", source_language="zh", context="context",
            profile=_profile(), for_dub=False, progress_cb=progress,
            cancel_check=cancelled.is_set)
    assert ai.calls == 1


@pytest.mark.asyncio
async def test_cancel_repair_preserves_checkpoint_and_resume_only_pending(monkeypatch, tmp_path):
    ai = _PartialTranslationAI()
    cancelled = asyncio.Event()
    monkeypatch.setattr(ls, "_profile_ai_route_clients_compat", lambda *_: [ai])
    kwargs = dict(target_language="vi", profile=_profile(glossary={}, qc_profile="strict"),
        user_settings=object(), for_dub=True, checkpoint_dir=tmp_path)
    async def progress(event):
        if event["event"] == "semantic_repair":
            assert event["pending_ids"] == ["p7"]
            cancelled.set()
    with pytest.raises(JobCancelledError):
        await ls.translate_transcript(_pressure_transcript(), **kwargs,
            progress_cb=progress, cancel_check=cancelled.is_set)
    assert ai.ids == [[f"p{i}" for i in range(12)]]
    saved = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))["batches"]["0"]
    assert [item["id"] for item in saved["items"]] == [f"p{i}" for i in range(12) if i != 7]
    assert saved["pending_ids"] == ["p7"]
    accepted = saved["items"]
    resumed = _PartialTranslationAI(repair=True)
    monkeypatch.setattr(ls, "_profile_ai_route_clients_compat", lambda *_: [resumed])
    result = await ls.translate_transcript(_pressure_transcript(), **kwargs, cancel_check=lambda: False)
    assert resumed.ids == [["p7"]]
    assert all(segment.translation_status == "success" for segment in result.segments)
    after = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))["batches"]["0"]
    assert [item for item in after["items"] if item["id"] != "p7"] == accepted


@pytest.mark.asyncio
async def test_cancel_between_batches_prevents_next_request(monkeypatch, tmp_path):
    ai = _EchoAI()
    cancelled = asyncio.Event()
    monkeypatch.setattr(ls, "_TRANSLATION_BATCH_SIZE", 1)
    async def progress(event):
        if event["event"] == "batch" and event["batch"] == 2:
            cancelled.set()
    with pytest.raises(JobCancelledError):
        await _translate(monkeypatch, ai, checkpoint_dir=tmp_path,
            progress_cb=progress, cancel_check=cancelled.is_set)
    assert ai.calls == 1
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["batches"]["0"]["items"][0]["id"] == "s1"
