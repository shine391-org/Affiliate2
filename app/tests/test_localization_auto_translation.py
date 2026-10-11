# SPDX-License-Identifier: AGPL-3.0-only
"""C6 Auto translation continuity: audited draft retention + bounded source recheck.

The AI provider is a MOCK chat double: this suite proves C6 policy/checkpoint
mechanics, NOT live provider behaviour. Canonical text, timeline and checkpoint
files are real. Source recheck stubs both the window extractor and the Whisper
service, so no paid provider and no real ASR runs here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.services import localization_service as svc


def _ids(prompt: str) -> list[str]:
    return re.findall(r'"id": "([^"]+)"', prompt)


class MockAI:
    """MOCK provider: one ordered handler, no network."""

    provider = SimpleNamespace(value="compatible")
    model = "mock"
    connection_name = "mock"
    max_retries = 0

    def __init__(self, handler):
        self.handler = handler
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        return self.handler(prompt, len(self.prompts))


def _install(monkeypatch, ai):
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *a, **k: [ai])


def _seg(sid, source, start, end, speaker="A"):
    return LocalizationSegment(id=sid, source_text=source, source_start_ms=start,
                               source_end_ms=end, speaker_id=speaker)


def _auto(**updates):
    base = LocalizationProfile(id="auto", name="Auto",
                               translation_automation="auto",
                               alignment_mode="off", diarization_mode="off")
    return base.model_copy(update=updates) if updates else base


def _strict(**updates):
    base = LocalizationProfile(id="strict", name="Strict", alignment_mode="off",
                               diarization_mode="off")
    return base.model_copy(update=updates) if updates else base


def _mixed_handler(prompt, call):
    """bad: structurally valid nonempty target rejected on localized_terms."""
    out = []
    for sid in _ids(prompt):
        if sid == "bad":
            out.append({"id": sid, "text": "Đây là bản dịch đúng nghĩa",
                        "localized_terms": {"品牌": "thương hiệu"}})
        else:
            out.append({"id": sid, "text": "Câu đúng"})
    return json.dumps(out, ensure_ascii=False)


def _mixed_transcript():
    return LocalizationTranscript(
        text="品牌很好 谢谢", language="zh",
        segments=[_seg("bad", "品牌很好", 0, 2000), _seg("ok", "谢谢", 2000, 4000)])


async def _run(tx, profile, monkeypatch, tmp_path, *, source_media=None, ai=None):
    if ai is not None:
        _install(monkeypatch, ai)
    return await svc.translate_transcript(
        tx, target_language="Vietnamese", profile=profile, user_settings=None,
        for_dub=True, checkpoint_dir=tmp_path, source_media=source_media)


# ── draft capture ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_captures_rejected_nonempty_target_as_audited_draft(monkeypatch, tmp_path):
    ai = MockAI(_mixed_handler)
    out = await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=ai)
    by_id = {s.id: s for s in out.segments}
    assert by_id["ok"].translation_status == "success"
    assert by_id["bad"].translation_status == "draft"
    assert by_id["bad"].translation_status != "success"
    assert by_id["bad"].translated_text == "Đây là bản dịch đúng nghĩa"
    audit = out.metadata["translation_auto_drafts"]["bad"]
    assert audit["source_text"] == "品牌很好"
    assert audit["target_text"] == "Đây là bản dịch đúng nghĩa"
    assert audit["issues"]
    assert audit["attempts"] >= 2, "initial + bounded semantic repair must be audited"
    assert audit["reasons"]


@pytest.mark.asyncio
async def test_auto_draft_audit_keeps_original_immutable_cue_and_timeline(monkeypatch, tmp_path):
    tx = _mixed_transcript()
    out = await _run(tx, _auto(), monkeypatch, tmp_path, ai=MockAI(_mixed_handler))
    bad = next(s for s in out.segments if s.id == "bad")
    assert (bad.source_text, bad.source_start_ms, bad.source_end_ms) == ("品牌很好", 0, 2000)
    audit = out.metadata["translation_auto_drafts"]["bad"]
    assert (audit["source_start_ms"], audit["source_end_ms"]) == (0, 2000)
    assert audit["speaker_id"] == "A"


@pytest.mark.asyncio
async def test_strict_never_creates_draft_and_still_fails_strict_qc(monkeypatch, tmp_path):
    ai = MockAI(_mixed_handler)
    _install(monkeypatch, ai)
    with pytest.raises(RuntimeError):
        await _run(_mixed_transcript(), _strict(), monkeypatch, tmp_path, ai=ai)
    # strict must not even carry the auto audit key semantics into a draft.


# ── transport / payload failures never draft ────────────────────────────────


@pytest.mark.asyncio
async def test_provider_transport_error_never_becomes_draft(monkeypatch, tmp_path):
    def boom(prompt, call):
        raise RuntimeError("503 Service Unavailable")
    ai = MockAI(boom)
    _install(monkeypatch, ai)
    with pytest.raises(RuntimeError):
        await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=ai)


@pytest.mark.parametrize("payload", [
    "not valid json",
    json.dumps([{"id": "bad", "text": "   "}, {"id": "ok", "text": "x"}], ensure_ascii=False),
    json.dumps([{"id": "bad", "text": "a"}, {"id": "bad", "text": "b"}], ensure_ascii=False),
    json.dumps([{"id": "bad", "text": "a"}], ensure_ascii=False),
    json.dumps([{"id": "wrong", "text": "a"}, {"id": "wrong2", "text": "b"}], ensure_ascii=False),
])
@pytest.mark.asyncio
async def test_malformed_empty_duplicate_missing_payload_never_draft(monkeypatch, tmp_path, payload):
    ai = MockAI(lambda prompt, call: payload)
    _install(monkeypatch, ai)
    try:
        out = await _run(_mixed_transcript(), _auto(qc_profile="standard"),
                         monkeypatch, tmp_path, ai=ai)
    except RuntimeError:
        return  # a structural payload failure stays an error, never a draft
    assert not out.metadata.get("translation_auto_drafts")
    assert all(s.translation_status != "draft" for s in out.segments)


# ── resume / invalidation / glossary safety ─────────────────────────────────


@pytest.mark.asyncio
async def test_resume_retains_audited_draft_but_never_promotes_it(monkeypatch, tmp_path):
    first = MockAI(_mixed_handler)
    _install(monkeypatch, first)
    await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=first)
    assert first.prompts

    second = MockAI(_mixed_handler)
    _install(monkeypatch, second)
    out = await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=second)
    bad = next(s for s in out.segments if s.id == "bad")
    assert bad.translation_status == "draft", "resumed draft must not become success"
    assert out.metadata["translation_auto_drafts"]["bad"]["issues"]
    assert not second.prompts, "audited draft must be restored, not re-translated"


@pytest.mark.asyncio
async def test_stale_source_invalidates_auto_checkpoint(monkeypatch, tmp_path):
    first = MockAI(_mixed_handler)
    _install(monkeypatch, first)
    await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=first)
    changed = _mixed_transcript()
    changed.segments[0].source_text = "品牌一般"
    second = MockAI(_mixed_handler)
    _install(monkeypatch, second)
    await _run(changed, _auto(), monkeypatch, tmp_path, ai=second)
    assert second.prompts, "changed source must invalidate the checkpoint"


@pytest.mark.asyncio
async def test_policy_switch_strict_invalidates_auto_checkpoint(monkeypatch, tmp_path):
    first = MockAI(_mixed_handler)
    _install(monkeypatch, first)
    await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=first)
    second = MockAI(_mixed_handler)
    _install(monkeypatch, second)
    await _run(_mixed_transcript(), _strict(qc_profile="standard"),
               monkeypatch, tmp_path, ai=second)
    assert second.prompts, "policy switch must invalidate the checkpoint"


@pytest.mark.asyncio
async def test_rejected_draft_never_poisons_job_glossary(monkeypatch, tmp_path):
    out = await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path,
                     ai=MockAI(_mixed_handler))
    assert "品牌" not in (out.metadata.get("localized_terms") or {})


# ── bounded source recheck (hypothesis only) ────────────────────────────────


class FakeWhisper:
    def __init__(self):
        self.calls: list[str] = []

    async def transcribe(self, path, language=None, quality="balanced", model_name=None, **k):
        self.calls.append(str(path))
        return {"text": "Nguồn giả thuyết", "language": language,
                "segments": [{"start": 0.0, "end": 1.0, "text": "Nguồn giả thuyết"}]}


def _stub_recheck(monkeypatch):
    import backend.services.whisper_service as ws
    fake = FakeWhisper()
    monkeypatch.setattr(ws, "whisper_service", fake)
    monkeypatch.setattr(ws.WhisperService, 'is_model_cached', classmethod(lambda cls,*a,**k:True))

    def extract(media, output, *, start_ms, end_ms):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"clip")
        return Path(output)

    monkeypatch.setattr(svc, "_extract_reference_clip_sync", extract)
    return fake


@pytest.mark.asyncio
async def test_source_recheck_runs_only_for_pending_and_never_replaces_source(monkeypatch, tmp_path):
    fake = _stub_recheck(monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    out = await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path,
                     ai=MockAI(_mixed_handler), source_media=source)
    assert len(fake.calls) == 1, "recheck must target only the pending draft ID"
    bad = next(s for s in out.segments if s.id == "bad")
    assert bad.source_text == "品牌很好", "source_text is immutable"
    audit = out.metadata["translation_auto_drafts"]["bad"]["source_recheck"]
    assert audit["hypothesis_text"] == "Nguồn giả thuyết"
    assert audit["status"] == "hypothesis"
    assert (bad.source_start_ms, bad.source_end_ms) == (0, 2000)


@pytest.mark.asyncio
async def test_source_recheck_skipped_without_source_media(monkeypatch, tmp_path):
    fake = _stub_recheck(monkeypatch)
    out = await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path,
                     ai=MockAI(_mixed_handler), source_media=None)
    assert not fake.calls
    assert not (out.metadata["translation_auto_drafts"]["bad"].get("source_recheck"))


@pytest.mark.asyncio
async def test_source_recheck_never_downloads_a_missing_model(monkeypatch,tmp_path):
    import backend.services.whisper_service as ws
    fake=_stub_recheck(monkeypatch)
    monkeypatch.setattr(ws.WhisperService,'is_model_cached',classmethod(lambda cls,*a,**k:False))
    out=await _run(_mixed_transcript(),_auto(),monkeypatch,tmp_path,
        ai=MockAI(_mixed_handler),source_media=tmp_path/'source.mp4')
    assert not fake.calls
    assert out.metadata['translation_auto_drafts']['bad']['source_recheck']['status']=='model_unavailable'


@pytest.mark.asyncio
async def test_source_recheck_caps_pending_windows(monkeypatch,tmp_path):
    import backend.services.whisper_service as ws
    fake=_stub_recheck(monkeypatch)
    monkeypatch.setattr(ws.WhisperService,'is_model_cached',classmethod(lambda cls,*a,**k:True))
    tx=LocalizationTranscript(language='zh',segments=[_seg(f's{i}','原文',i*2000,(i+1)*2000) for i in range(8)])
    audits={s.id:{} for s in tx.segments}
    await svc._recheck_pending_sources(tx,[s.id for s in tx.segments],audits,
        source_media=tmp_path/'source.mp4',profile=_auto())
    assert 0<len(fake.calls)<=3
    assert any(a.get('source_recheck',{}).get('status')=='budget_exhausted' for a in audits.values())


@pytest.mark.asyncio
async def test_auto_final_global_guard_keeps_target_as_draft_not_source_fallback(monkeypatch,tmp_path):
    real=svc._translation_guard_checks
    def checked(batch,*args,**kwargs):
        checks=real(batch,*args,**kwargs)
        if all(s.translation_status=='success' for s in batch):
            checks[batch[0].id]=[{'kind':'quantity_lost','severity':'critical'}]
        return checks
    monkeypatch.setattr(svc,'_translation_guard_checks',checked)
    ai=MockAI(lambda prompt,n:json.dumps([{'id':sid,'text':'Câu đúng'} for sid in _ids(prompt)]))
    out=await _run(_mixed_transcript(),_auto(),monkeypatch,tmp_path,ai=ai)
    assert out.segments[0].translation_status=='draft'
    assert out.segments[0].translated_text=='Câu đúng'
    assert out.metadata['translation_auto_drafts'][out.segments[0].id]['issues']


@pytest.mark.asyncio
async def test_final_guard_draft_removes_its_terms_from_checkpoint_glossary(monkeypatch,tmp_path):
    real=svc._translation_guard_checks
    def checked(batch,*args,**kwargs):
        checks=real(batch,*args,**kwargs)
        if all(s.translation_status=='success' for s in batch):
            checks[batch[0].id]=[{'kind':'quantity_lost','severity':'critical'}]
        return checks
    monkeypatch.setattr(svc,'_translation_guard_checks',checked)
    def answer(prompt,n):
        return json.dumps([{'id':sid,'text':'Thương hiệu này tốt.' if sid=='bad' else 'Cảm ơn.',
            'localized_terms':{'品牌':'Thương hiệu'} if sid=='bad' else {}} for sid in _ids(prompt)],ensure_ascii=False)
    out=await _run(_mixed_transcript(),_auto(),monkeypatch,tmp_path,ai=MockAI(answer))
    assert out.segments[0].translation_status=='draft'
    assert '品牌' not in out.metadata['localized_terms']
    saved=json.loads((tmp_path/'checkpoint.json').read_text(encoding='utf-8'))
    assert all('品牌' not in item.get('localized_terms',{})
        for batch in saved['batches'].values() for item in batch.get('items',[]))


@pytest.mark.asyncio
async def test_source_recheck_timeout_does_not_hold_the_canonical_compute_gate(monkeypatch,tmp_path):
    import asyncio
    import backend.core.tool_runners as runner
    import backend.services.whisper_service as ws
    from contextlib import asynccontextmanager
    fake=_stub_recheck(monkeypatch);lock=asyncio.Lock();release=asyncio.Event()
    @asynccontextmanager
    async def gate():
        async with lock:yield
    async def stalled(path,**kwargs):
        await release.wait();return {'segments':[{'text':'Hypothesis'}]}
    monkeypatch.setattr(runner,'localization_gpu_gate',gate)
    monkeypatch.setattr(fake,'transcribe',stalled)
    monkeypatch.setattr(svc,'_AUTO_RECHECK_TIMEOUT_S',0.15)
    monkeypatch.setattr(svc,'_AUTO_RECHECK_TASKS',set())
    tx=LocalizationTranscript(language='zh',segments=[_seg('a','原文',0,2000)])
    audits={'a':{}}
    try:
        await svc._recheck_pending_sources(tx,['a'],audits,
            source_media=tmp_path/'source.mp4',profile=_auto())
        assert audits['a']['source_recheck']['status']=='timeout'
        assert not lock.locked(), 'Optional timed-out recheck must not block downstream C7/render'
    finally:
        release.set();await asyncio.gather(*list(svc._AUTO_RECHECK_TASKS))


@pytest.mark.asyncio
async def test_source_recheck_timeout_keeps_private_files_until_producer_settles(monkeypatch,tmp_path):
    import asyncio
    import backend.services.whisper_service as ws
    fake=_stub_recheck(monkeypatch)
    release=asyncio.Event()
    async def stalled(path,**kwargs):
        fake.calls.append(str(path))
        await release.wait()
        assert Path(path).is_file(), 'Timeout must not delete a live producer input'
        return {'segments':[{'text':'假设'}]}
    monkeypatch.setattr(fake,'transcribe',stalled)
    monkeypatch.setattr(svc,'_AUTO_RECHECK_TIMEOUT_S',0.15)
    monkeypatch.setattr(svc,'_AUTO_RECHECK_TASKS',set())
    tx=LocalizationTranscript(language='zh',segments=[_seg('a','原文',0,2000),_seg('b','原文',2000,4000)])
    audits={'a':{},'b':{}}
    try:
        await asyncio.wait_for(svc._recheck_pending_sources(tx,['a','b'],audits,
            source_media=tmp_path/'source.mp4',profile=_auto()),timeout=2)
        assert audits['a']['source_recheck']['status']=='timeout'
        assert audits['b']['source_recheck']['status']=='budget_exhausted'
        assert len(fake.calls)==1 and Path(fake.calls[0]).is_file()
    finally:
        release.set()
        await asyncio.gather(*list(svc._AUTO_RECHECK_TASKS))
    assert not svc._AUTO_RECHECK_TASKS
    assert not Path(fake.calls[0]).parent.exists()

