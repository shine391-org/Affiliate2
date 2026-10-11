"""Fast mode canonical routing and invalidation regression gates."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import asyncio

import pytest

from backend.api import localization as api
from backend.core.localization import LocalizationProfile
from backend.services import localization_service as service


def test_audio_mode_changes_invalidate_source_of_asr():
    assert api._profile_invalidation({"audio_mode"}) == "separation"
    assert api._profile_invalidation({"preserve_background", "background_gain_db"}) == "audio_mix"


def test_asr_binding_is_compared_and_explicitly_adopted():
    old = {"asr_provider": "faster_whisper", "asr_model": "large-v3"}
    new = {**old, "asr_model": "medium", "asr_connection_id": None,
           "asr_fallback_to_local": False, "asr_local_model": "large-v3"}
    diff = api._binding_diff(old, new, is_dub=False)
    assert "asr" in diff["changed_stages"]
    assert diff["from_stage"] == "asr"
    updates = api._binding_profile_updates(new, is_dub=False)
    assert updates["asr_model"] == "medium"
    assert "preserve_background" not in updates


@pytest.mark.asyncio
async def test_job_cancel_while_remote_waits_cancels_request_without_fallback(monkeypatch):
    import sys
    from backend.core.exceptions import JobCancelledError
    closed = []
    class Error(RuntimeError):
        pass
    async def remote(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    async def cancelled():
        return True
    monkeypatch.setitem(sys.modules, "backend.services.groq_asr_service", SimpleNamespace(transcribe_groq=remote, GroqASRError=Error))
    profile = SimpleNamespace(asr_provider="groq", asr_model="whisper-large-v3-turbo", asr_connection_id="saved", asr_fallback_to_local=True, asr_local_model="medium", alignment_mode="off", diarization_mode="off")
    with pytest.raises(JobCancelledError):
        await asyncio.wait_for(service.transcribe_for_localization("unused.mp4", profile, cancel_check=cancelled), 2)
    assert closed == [True]


@pytest.mark.asyncio
async def test_local_asr_exact_model_and_metadata(monkeypatch):
    from backend.services.whisper_service import whisper_service
    events = []
    @asynccontextmanager
    async def gate():
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")
    async def transcribe(path, **kwargs):
        assert events[-1] == "lock"
        assert kwargs["model_name"] == "medium"
        return {"text": "Hello.", "language": "en", "segments": [{"start": 0, "end": 1, "text": "Hello."}]}
    monkeypatch.setattr(whisper_service, "transcribe", transcribe)
    profile = LocalizationProfile(id="p", name="Local", asr_model="medium", alignment_mode="off", diarization_mode="off")
    tx = await service.transcribe_for_localization("unused.mp4", profile, gpu_gate=gate)
    assert events == ["lock", "unlock"]
    assert tx.metadata["asr"]["provider"] == "faster_whisper"
    assert tx.metadata["asr"]["model"] == "medium"


@pytest.mark.asyncio
async def test_remote_asr_never_takes_gpu_gate(monkeypatch):
    import sys
    events = []
    @asynccontextmanager
    async def gate():
        events.append("lock")
        yield
    async def remote(*args, **kwargs):
        return service.localization_transcript_from_source(text="Hello", language="en", source="groq", raw_segments=[{"start":0,"end":1,"text":"Hello"}])
    class Error(RuntimeError):
        pass
    monkeypatch.setitem(sys.modules, "backend.services.groq_asr_service", SimpleNamespace(transcribe_groq=remote, GroqASRError=Error))
    profile = SimpleNamespace(asr_provider="groq", asr_model="whisper-large-v3-turbo", asr_connection_id="saved", asr_fallback_to_local=False, asr_local_model="medium", alignment_mode="off", diarization_mode="off")
    tx = await service.transcribe_for_localization("unused.mp4", profile, gpu_gate=gate)
    assert not events
    assert tx.metadata["asr"]["provider"] == "groq"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,eligible", [(False, True), (True, False), (True, True)])
async def test_fallback_requires_explicit_optin_and_retryable_failure(monkeypatch, enabled, eligible):
    import sys
    from backend.services.whisper_service import whisper_service
    events = []
    class Error(RuntimeError):
        fallback_eligible = eligible
    async def remote(*args, **kwargs):
        assert not events
        raise Error("Groq timeout" if eligible else "Groq credential invalid")
    @asynccontextmanager
    async def gate():
        events.append("lock")
        yield
        events.append("unlock")
    async def local(path, **kwargs):
        assert kwargs["model_name"] == "medium"
        assert events == ["lock"]
        return {"text":"Hello", "language":"en", "segments":[{"start":0,"end":1,"text":"Hello"}]}
    monkeypatch.setitem(sys.modules, "backend.services.groq_asr_service", SimpleNamespace(transcribe_groq=remote, GroqASRError=Error))
    monkeypatch.setattr(whisper_service, "transcribe", local)
    profile = SimpleNamespace(asr_provider="groq", asr_model="whisper-large-v3-turbo", asr_quality="best", asr_connection_id="saved", asr_fallback_to_local=enabled, asr_local_model="medium", alignment_mode="off", diarization_mode="off")
    if enabled and eligible:
        tx = await service.transcribe_for_localization("unused.mp4", profile, gpu_gate=gate)
        assert tx.metadata["asr"]["model"] == "medium"
        assert tx.metadata["asr"]["fallback_reason"] == "Groq timeout"
        assert events == ["lock", "unlock"]
    else:
        with pytest.raises(Error):
            await service.transcribe_for_localization("unused.mp4", profile, gpu_gate=gate)
        assert not events
