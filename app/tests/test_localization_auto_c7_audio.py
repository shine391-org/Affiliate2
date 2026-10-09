"""LOCAL integration: real WAV/FFmpeg composition, only TTS provider mocked."""
from pathlib import Path
import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from tests.test_localization_postfit_measurement import audio, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed', [False, True])
async def test_auto_draft_speech_is_not_silently_omitted(tmp_path, monkeypatch, mixed):
    calls = []
    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        audio(Path(output_path), 1000)
        return 1000
    monkeypatch.setattr(svc, '_generate_tts_chunk', generate)
    monkeypatch.setattr(svc, '_tts_config', lambda *_: runtime())
    source = audio(tmp_path / 'source.wav', 5000)
    draft = LocalizationSegment(id='d', source_text='三公斤', translated_text='4 kg.',
        source_start_ms=0, source_end_ms=2500, translation_status='draft', qc_status='review')
    segments = [draft]
    if mixed:
        segments.append(LocalizationSegment(id='s', source_text='谢谢', translated_text='Cảm ơn.',
            source_start_ms=2500, source_end_ms=5000, translation_status='success'))
    tx = LocalizationTranscript(language='zh', segments=segments, metadata={
        'translation_auto_drafts': {'d': {'source_text': draft.source_text,
            'target_text': draft.translated_text, 'issues': [{'kind': 'quantity_lost'}]}}})
    result = await svc.synthesize_timed_dub(tx, source_media=source, target_language='Vietnamese',
        profile=LocalizationProfile(id='qa', name='Auto', translation_automation='auto',
            dub_rewrite_attempts=0, video_retime_mode='off'), user_settings=object(), work_dir=tmp_path/'work')
    planned = {item.segment_id for item in result.plan}
    assert planned == {s.id for s in segments}, 'Draft cues must not disappear from speech planning'
    assert any('4' in text for text in calls), 'Real generated provider input includes draft target speech'
    d = next(s for s in result.transcript.segments if s.id == 'd')
    assert d.translation_status == 'draft'
    assert d.qc_status == 'review', 'Successful timing must not wash away unresolved fidelity'
    assert result.transcript.metadata['translation_auto_drafts']['d']['issues']
    assert result.dub_track.exists()
    assert await svc._audio_duration_ms(result.dub_track) >= 4900


@pytest.mark.asyncio
async def test_strict_c7_refuses_draft_before_any_tts(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('Strict C7 must never synthesize an unapproved draft')
    monkeypatch.setattr(svc, '_generate_tts_chunk', forbidden)
    monkeypatch.setattr(svc, '_tts_config', lambda *_: runtime())
    source = audio(tmp_path/'source.wav', 3000)
    tx = LocalizationTranscript(segments=[LocalizationSegment(id='d', source_text='三公斤',
        translated_text='4 kg.', source_start_ms=0, source_end_ms=2500, translation_status='draft')])
    with pytest.raises(RuntimeError, match='translated speech|draft'):
        await svc.synthesize_timed_dub(tx, source_media=source, target_language='Vietnamese',
            profile=LocalizationProfile(id='strict', name='Strict', dub_rewrite_attempts=0),
            user_settings=object(), work_dir=tmp_path/'work')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['missing', 'stale_source', 'stale_target', 'empty_issues', 'empty_target'])
async def test_auto_c7_requires_current_nonempty_draft_audit_before_tts(tmp_path, monkeypatch, invalid):
    async def forbidden(*args, **kwargs):
        pytest.fail('Invalid draft must be rejected before synthesizing any neighbor')
    monkeypatch.setattr(svc, '_generate_tts_chunk', forbidden)
    monkeypatch.setattr(svc, '_tts_config', lambda *_: runtime())
    source = audio(tmp_path/'source.wav', 5000)
    draft = LocalizationSegment(id='d', source_text='三公斤', translated_text='4 kg.',
        source_start_ms=0, source_end_ms=2500, translation_status='draft')
    accepted = LocalizationSegment(id='s', source_text='谢谢', translated_text='Cảm ơn.',
        source_start_ms=2500, source_end_ms=5000, translation_status='success')
    record = {'source_text': draft.source_text, 'target_text': draft.translated_text,
        'issues': [{'kind': 'quantity_lost'}]}
    if invalid == 'stale_source': record['source_text'] = '四公斤'
    if invalid == 'stale_target': record['target_text'] = '5 kg.'
    if invalid == 'empty_issues': record['issues'] = []
    if invalid == 'empty_target':
        draft.translated_text = '   '
        record['target_text'] = draft.translated_text
    tx = LocalizationTranscript(segments=[draft, accepted], metadata={
        'translation_auto_drafts': {} if invalid == 'missing' else {'d': record}})
    with pytest.raises(RuntimeError, match='issue audit'):
        await svc.synthesize_timed_dub(tx, source_media=source, target_language='Vietnamese',
            profile=LocalizationProfile(id='auto', name='Auto', translation_automation='auto', dub_rewrite_attempts=0),
            user_settings=object(), work_dir=tmp_path/'work')


@pytest.mark.asyncio
async def test_guarded_draft_rewrite_preserves_current_audit_qc_persistence_and_resume(tmp_path, monkeypatch):
    import json
    from tests.test_localization_auto_draft_integration import video
    from backend.services import localization_qc_service as qc
    # Exact before/after wording from the real commons:164833398 failure.
    before = 'cộng thêm 2 loại cà phê có hương vị khác nhau'
    after = 'thêm 2 loại cà phê khác vị'
    calls = []
    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        duration = 3500 if text == before else 1100
        audio(Path(output_path), duration)
        return duration
    class AI:
        max_retries = 0
        async def chat(self, **kwargs):
            return json.dumps([{'id': 'd', 'text': after}])
    monkeypatch.setattr(svc, '_generate_tts_chunk', generate)
    monkeypatch.setattr(svc, '_tts_config', lambda *_: runtime())
    monkeypatch.setattr(svc, '_profile_ai_route_clients_compat', lambda *_: [AI()])
    source = video.__wrapped__(tmp_path)
    seg = LocalizationSegment(id='d', source_text='加上兩種不同口味的咖啡', translated_text=before,
        source_start_ms=0, source_end_ms=2000, translation_status='draft', qc_status='review',
        overrides={'target_start_ms': 0, 'target_end_ms': 2000})
    issue = {'kind': 'quantity_lost', 'severity': 'hard', 'action': 'reject'}
    tx = LocalizationTranscript(language='zh', segments=[seg], metadata={
        'translation_auto_drafts': {'d': {'source_text': seg.source_text, 'target_text': before,
            'issues': [issue], 'attempts': 2}}})
    profile = LocalizationProfile(id='auto', name='Auto', translation_automation='auto',
        dub_rewrite_attempts=1, video_retime_mode='off', diarization_mode='off')
    args = dict(source_media=source, target_language='Vietnamese', profile=profile,
        user_settings=object(), work_dir=tmp_path/'work', reuse_existing_chunks=True)
    result = await svc.synthesize_timed_dub(tx, **args)
    assert result.rewrite_count == 1 and calls == [before, after]
    audit = result.transcript.metadata['translation_auto_drafts']['d']
    assert audit['target_text'] == after, 'A guarded C7 rewrite must not leave a stale C6 draft audit'
    assert audit['source_text'] == seg.source_text and audit['issues'] == [issue] and audit['attempts'] == 2
    assert audit['dubbing_rewrites'][-1]['before_target_text'] == before
    assert audit['dubbing_rewrites'][-1]['target_text'] == after
    report = qc.evaluate_localization_qc(source_video=source, final_video=source,
        transcript=result.transcript, profile=profile, check_tts_quality=True)
    assert report.status == 'NEEDS_REVIEW' and not report.failures, report.as_dict()
    assert next(w for w in report.warnings if w['code'] == 'translation_auto_draft')['target_text'] == after
    persisted = svc.persist_dubbing_rewrites(tx, result.transcript)
    assert persisted.metadata['translation_auto_drafts']['d'] == audit
    assert persisted.segments[0].translated_text == after
    assert persisted.segments[0].translation_status == 'draft'
    # Exercise accepted_text replay from ORIGINAL C6, and persisted-C6 reuse.
    for initial in (tx, persisted):
        resumed = await svc.synthesize_timed_dub(initial, **args)
        assert resumed.transcript.metadata['translation_auto_drafts']['d']['target_text'] == after
        assert resumed.transcript.segments[0].translation_status == 'draft'
    assert calls == [before, after], 'No new provider audio is needed for matching accepted text'
    assert tx.metadata['translation_auto_drafts']['d']['target_text'] == before


@pytest.mark.asyncio
async def test_slow_rewrite_preserves_last_good_audio_and_approved_text(tmp_path, monkeypatch):
    """AI-shorter words do not imply shorter TTS; reject after actual audio probe."""
    import json
    from tests.test_localization_auto_draft_integration import video
    from backend.services.localization_service import LocalizationTimingError

    before = 'cộng thêm 2 loại cà phê có hương vị khác nhau'
    after = 'thêm 2 loại cà phê khác vị'
    calls = []

    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        duration = 3500 if text == before else 4000
        audio(Path(output_path), duration)
        return duration

    class AI:
        max_retries = 0
        async def chat(self, **kwargs):
            return json.dumps([{'id': 's', 'text': after}], ensure_ascii=False)

    monkeypatch.setattr(svc, '_generate_tts_chunk', generate)
    monkeypatch.setattr(svc, '_tts_config', lambda *_: runtime())
    monkeypatch.setattr(svc, '_profile_ai_route_clients_compat', lambda *_: [AI()])
    source = video.__wrapped__(tmp_path)
    seg = LocalizationSegment(
        id='s', source_text='加上兩種不同口味的咖啡',
        translated_text=before, source_start_ms=0, source_end_ms=2000,
        translation_status='success',
        overrides={'target_start_ms': 0, 'target_end_ms': 2000},
    )
    tx = LocalizationTranscript(language='zh', segments=[seg])
    profile = LocalizationProfile(
        id='measured-reject', name='Measured reject',
        translation_automation='auto', dub_rewrite_attempts=1,
        video_retime_mode='off', diarization_mode='off',
    )
    work = tmp_path/'work'
    with pytest.raises(LocalizationTimingError):
        await svc.synthesize_timed_dub(
            tx, source_media=source, target_language='Vietnamese',
            profile=profile, user_settings=object(), work_dir=work,
        )

    # The candidate is measured, but cannot replace better audio or C6 text.
    assert calls == [before, after]
    assert tx.segments[0].translated_text == before
    assert abs(await svc._audio_duration_ms(work/'chunk_0001.wav') - 3500) <= 15
    assert not list(work.glob('chunk_0001.rewrite-*'))
    state = json.loads((work/'dubbing_state.json').read_text(encoding='utf-8'))
    assert not state.get('accepted_text')
    assert state['rewrite_count'] == 0
    rejected = [row for row in state['rewrite_audit']
                if row.get('status') == 'rejected_measured']
    assert len(rejected) == 1
    assert rejected[0]['before'] == before and rejected[0]['after'] == after
    assert rejected[0]['duration_after_ms'] == 4000
