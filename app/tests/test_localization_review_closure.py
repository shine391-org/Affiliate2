"""Regressions reproduced in the 2026-10-09 source/timing review."""
import json
from pathlib import Path

import pytest

from backend.services import localization_service as svc
from backend.services.dubbing_planner import (
    DubbingChunk, DubbingConfig, PlanItem, allocate_silence, required_speed,
)
from backend.services.dubbing_rewrite_guard import check_rewrite_meaning, check_translation_meaning
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript


@pytest.mark.parametrize('durations', [(2100, 1300), (1300, 2100)])
def test_shared_gap_satisfies_both_hard_demands_before_comfort_speed(durations):
    plan = [PlanItem(0, 'a', 0, 1000, 'a', 'a'), PlanItem(1, 'b', 2000, 3000, 'b', 'b')]
    chunks = [DubbingChunk(1, [0], 0, 1000, actual_duration_ms=durations[0]),
              DubbingChunk(2, [1], 2000, 3000, actual_duration_ms=durations[1])]
    allocate_silence(plan, chunks, DubbingConfig(), timeline_end_ms=3000)
    assert all(required_speed(c.actual_duration_ms, c.available_ms) <= 1.30 for c in chunks)
    assert chunks[1].start_ms - chunks[0].end_ms >= 350
    first = [(c.start_ms, c.end_ms) for c in chunks]
    allocate_silence(plan, chunks, DubbingConfig(), timeline_end_ms=3000)
    assert first == [(c.start_ms, c.end_ms) for c in chunks]


@pytest.mark.parametrize('before,after', [
    ('Tôi mua chiếc xe này.', 'Tôi bán chiếc xe này.'),
    ('Sản phẩm chống nước.', 'Sản phẩm thấm nước.'),
    ('Tôi cho anh ấy vay tiền.', 'Tôi vay tiền của anh ấy.'),
])
def test_changed_transaction_or_property_is_rejected(before, after):
    assert check_rewrite_meaning(before, after, 'Vietnamese')
    assert check_rewrite_meaning(after, before, 'Vietnamese')


@pytest.mark.parametrize('source,target', [
    ('我买了这辆车。', 'Tôi bán chiếc xe này.'),
    ('这款产品防水。', 'Sản phẩm thấm nước.'),
    ('我借钱给他。', 'Tôi vay tiền của anh ấy.'),
])
def test_c6_changed_transaction_or_property_is_rejected(source, target):
    assert check_translation_meaning(source, target, 'Chinese', 'Vietnamese', glossary={})


@pytest.mark.parametrize('before,after', [
    ('Tôi mua chiếc xe này.', 'Tôi mua xe này.'),
    ('Sản phẩm này chống nước.', 'Sản phẩm chống nước.'),
    ('Tôi cho anh ấy vay tiền.', 'Tôi cho anh ấy mượn tiền.'),
    ('Tôi mua và bán xe.', 'Tôi bán và mua xe.'),
])
def test_equivalent_transaction_or_property_is_allowed(before, after):
    assert not check_rewrite_meaning(before, after, 'Vietnamese')


@pytest.mark.parametrize('target', ['thêm 2 loại cà phê khác vị', 'thêm 2 loại cà phê không giống nhau'])
def test_lexical_difference_is_not_a_lost_clause_negation(target):
    assert not check_translation_meaning('加上兩種不同口味的咖啡', target,
        'Chinese', 'Vietnamese', glossary={})
    assert check_translation_meaning('不是不同口味的咖啡', 'Cà phê khác vị',
        'Chinese', 'Vietnamese', glossary={})


@pytest.mark.parametrize('fail_suffix', ['', '.words.json', '.input.json'])
def test_chunk_bundle_promotion_failure_restores_last_good(tmp_path, monkeypatch, fail_suffix):
    old, new = tmp_path / 'old.wav', tmp_path / 'new.wav'
    for path, label in [(old, 'old'), (new, 'new')]:
        path.write_bytes(label.encode())
        path.with_name(path.name + '.words.json').write_text(label)
        svc._write_chunk_identity(path, label, 1000)
    original = {suffix: old.with_name(old.name + suffix).read_bytes()
                for suffix in ('', '.words.json', '.input.json')}
    replace = Path.replace
    def fail_once(path, target):
        if path == new.with_name(new.name + fail_suffix):
            raise OSError('injected promotion failure')
        return replace(path, target)
    monkeypatch.setattr(Path, 'replace', fail_once)
    with pytest.raises(OSError, match='injected'):
        svc._promote_chunk_bundle(new, old)
    assert all(old.with_name(old.name + s).read_bytes() == data for s, data in original.items())
    assert svc._chunk_identity_matches(old, 'old')


def test_chunk_bundle_success_removes_obsolete_words_and_binds_new_audio(tmp_path):
    old, new = tmp_path / 'old.wav', tmp_path / 'new.wav'
    old.write_bytes(b'old')
    old.with_name(old.name + '.words.json').write_text('obsolete events')
    svc._write_chunk_identity(old, 'old', 1000)
    new.write_bytes(b'new')
    svc._write_chunk_identity(new, 'new', 900)
    svc._promote_chunk_bundle(new, old)
    assert old.read_bytes() == b'new'
    assert not old.with_name(old.name + '.words.json').exists()
    assert svc._chunk_identity_matches(old, 'new')


def test_missing_candidate_never_deletes_last_good_audio(tmp_path):
    old = tmp_path / 'old.wav'
    old.write_bytes(b'old')
    svc._write_chunk_identity(old, 'old', 1000)
    with pytest.raises(FileNotFoundError):
        svc._promote_chunk_bundle(tmp_path / 'absent.wav', old)
    assert svc._chunk_identity_matches(old, 'old')


@pytest.mark.asyncio
async def test_semantic_repair_uses_configured_fallback_only_for_rejected_id(monkeypatch, tmp_path):
    calls = []
    class AI:
        def __init__(self, name):
            self.connection_name = name
        async def chat(self, messages, **kwargs):
            prompt = messages[0]['content']
            rows = json.loads(prompt.split('SEGMENTS:\n', 1)[1].split('\n\nReturn ONLY', 1)[0])
            calls.append((self.connection_name, [row['id'] for row in rows]))
            return json.dumps([{'id': row['id'], 'text':
                ('Tôi mua xe này.' if self.connection_name == 'fallback' else 'Tôi bán xe này.')
                if row['id'] == 'bad' else 'Cảm ơn'} for row in rows])
    monkeypatch.setattr(svc, '_profile_ai_route_clients_compat', lambda *a: [AI('primary'), AI('fallback')])
    tx = LocalizationTranscript(language='zh', segments=[
        LocalizationSegment(id='bad', source_text='我买了这辆车。', source_start_ms=0, source_end_ms=2000),
        LocalizationSegment(id='good', source_text='谢谢', source_start_ms=2000, source_end_ms=3000)])
    result = await svc.translate_transcript(tx, target_language='Vietnamese',
        profile=LocalizationProfile(id='p', name='p', qc_profile='strict'),
        user_settings=None, for_dub=True, checkpoint_dir=tmp_path)
    assert calls == [('primary', ['bad', 'good']), ('primary', ['bad']), ('fallback', ['bad'])]
    assert all(s.translation_status == 'success' for s in result.segments)
    assert result.segments[0].translated_text == 'Tôi mua xe này.'


@pytest.mark.asyncio
async def test_targeted_c6_repair_receives_neighbours_missing_from_global_context(monkeypatch):
    before = LocalizationSegment(id='q', source_text='你多大', source_start_ms=0, source_end_ms=1000)
    answer = LocalizationSegment(id='a', source_text='我23了', source_start_ms=1000, source_end_ms=2000)
    prompts = []
    async def chat(*args, messages, **kwargs):
        prompts.append(messages[0]['content'])
        return '[{"id":"a","text":"Tôi 23 tuổi rồi"}]'
    monkeypatch.setattr(svc, '_call_ai_chat_resilient', chat)
    texts, failed = await svc._translate_batch_resilient(None, [answer],
        target_language='Vietnamese', context='...[context truncated]...',
        profile=LocalizationProfile(id='p', name='p'), for_dub=True,
        source_language='zh', context_segments=[before, answer])
    assert not failed and texts == ['Tôi 23 tuổi rồi']
    assert '你多大' in prompts[0]


@pytest.mark.asyncio
async def test_semantic_fallback_exhaustion_retains_only_last_candidate_audit(monkeypatch):
    from types import SimpleNamespace
    clients = [SimpleNamespace(connection_name=str(i)) for i in range(3)]
    route = svc._AIStageRouteState(clients)
    calls = []
    async def chat(*args, route_state, **kwargs):
        calls.append(route_state.current.connection_name)
        return json.dumps([{'id': 's', 'text': 'Tôi bán xe này' + '.' * len(calls)}])
    monkeypatch.setattr(svc, '_call_ai_chat_resilient', chat)
    segment = LocalizationSegment(id='s', source_text='我买了这辆车。', source_start_ms=0, source_end_ms=2000)
    drafts = {}
    _, failed = await svc._translate_batch_resilient(clients[0], [segment], route_state=route,
        target_language='Vietnamese', context='', profile=LocalizationProfile(id='p', name='P'),
        for_dub=True, source_language='zh', drafts=drafts)
    assert calls == ['0', '0', '1', '2'] and failed == {0}
    assert drafts['s']['semantic_attempts'] == 4
    assert drafts['s']['target_text'] == 'Tôi bán xe này....'


@pytest.mark.asyncio
async def test_c7_checks_source_even_if_candidate_preserves_wrong_c6_predicate(monkeypatch):
    segment = LocalizationSegment(id='s', source_text='我买了这辆车。',
        translated_text='Tôi bán chiếc xe này.', source_start_ms=0, source_end_ms=1000)
    plan = [PlanItem(0, 's', 0, 1000, segment.translated_text, segment.translated_text)]
    chunk = DubbingChunk(1, [0], 0, 1000, actual_duration_ms=2000)
    async def chat(*args, **kwargs):
        return '[{"id":"s","text":"Tôi bán xe này."}]'
    monkeypatch.setattr(svc, '_call_ai_chat_resilient', chat)
    issues = []
    changed = await svc._adapt_chunk_items(object(), plan=plan, chunk=chunk, segment_by_id={'s': segment},
        target_language='Vietnamese', target_ms=1000, profile=LocalizationProfile(id='p', name='P'),
        state={'source_language': 'zh', 'measured_duration_ms': 2000}, issues=issues)
    assert not changed and segment.translated_text == 'Tôi bán chiếc xe này.'
    assert any(v.get('basis') == 'source' and v['kind'] == 'predicate_reversal'
               for i in issues for v in i.get('violations', []))


@pytest.mark.parametrize('reason', ['multi_cue_unverified_timing', 'rewrite_tts_unavailable'])
def test_qc_retains_exact_recovery_warning(monkeypatch, tmp_path, reason):
    from backend.services import localization_qc_service as qc
    from test_phase_c_localization_qc import _media, _tx
    monkeypatch.setattr(qc, 'probe_media', lambda _: _media())
    monkeypatch.setattr(qc, 'audio_max_volume_db', lambda _: -1.0)
    report = qc.evaluate_localization_qc(source_video=tmp_path/'s.mp4', final_video=tmp_path/'f.mp4',
        transcript=_tx(), profile=LocalizationProfile(id='p', name='P'), check_tts_quality=False,
        timing_issues=[{'reason': reason, 'chunk_id': 1, 'segment_ids': ['s1']}])
    assert report.status == 'NEEDS_REVIEW'
    assert any(w['code'] == reason and w['chunk_id'] == 1 for w in report.warnings)
