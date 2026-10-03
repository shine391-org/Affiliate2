# SPDX-License-Identifier: AGPL-3.0-only
import json
from types import SimpleNamespace

import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider


def tx(text='Chị 橙子 gặp 吴小彤'):
    return LocalizationTranscript(segments=[LocalizationSegment(id='a', source_text='橙子 吴小彤',
        translated_text=text, source_start_ms=0, source_end_ms=3000, translation_status='success')])


def runtime(voice='vi-VN-HoaiMyNeural'):
    return svc.TTSRuntimeConfig(provider=TTSProvider.EDGE_TTS,default_voice=voice,api_key='',model=None,base_url=None,region=None)


@pytest.mark.asyncio
async def test_canonical_pipeline_prepares_names_before_tts(tmp_path,monkeypatch):
    import wave
    monkeypatch.setattr(svc,'_tts_config',lambda *_: runtime())
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_: [object()])
    monkeypatch.setattr('backend.services.video_utils.probe_duration',lambda *_: 5.0)
    async def chat(*a,**kw): return '[{"id":"吴小彤","text":"Wu Xiaotong"}]'
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',chat)
    spoken=[]
    async def generate(text,*,output_path,**kwargs):
        spoken.append(text)
        with wave.open(str(output_path),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
            w.writeframes(b'\x01\x00'*16000)
        return 1000
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    result=await svc.synthesize_timed_dub(tx('吴小彤'),source_media=tmp_path/'source.mp4',
        target_language='Vietnamese',profile=LocalizationProfile(id='p',name='p',dub_rewrite_attempts=0),
        user_settings=object(),work_dir=tmp_path/'dub')
    assert spoken==['Wu Xiaotong']
    assert result.transcript.segments[0].qc_status=='review'


@pytest.mark.asyncio
async def test_spoken_forms_are_spoken_only_and_cache_validated_result(tmp_path, monkeypatch):
    calls=[]
    async def chat(*a, **kw):
        calls.append(kw)
        return json.dumps([{'id':'橙子','text':'Cheng Zi'},{'id':'吴小彤','text':'Wu Xiaotong'}])
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',chat)
    profile=LocalizationProfile(id='p',name='p')
    original=tx()
    for _ in range(2):
        out=original.model_copy(deep=True)
        issues=await svc._prepare_dubbing_speech(out,profile=profile,runtime=runtime(),ai=object(),
            fallback_ai=None,route_state=None,work_dir=tmp_path,progress_cb=None)
        # Approved text is untouched; only the spoken form and its provenance change.
        assert out.segments[0].translated_text=='Chị 橙子 gặp 吴小彤'
        assert issues[0]['spoken_text']=='Chị Cheng Zi gặp Wu Xiaotong'
        assert issues[0]['before']=='Chị 橙子 gặp 吴小彤'
        assert out.segments[0].source_text==original.segments[0].source_text
        assert issues[0]['reason']=='speech_pronunciation_normalized'
        assert issues[0]['requires_human_review'] is True
    assert len(calls)==1
    assert original.segments[0].translated_text=='Chị 橙子 gặp 吴小彤'


@pytest.mark.asyncio
async def test_glossary_precedence_and_changed_glossary_invalidates_cache(tmp_path, monkeypatch):
    async def forbidden(*a,**kw):
        pytest.fail('Glossary needs no AI')
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',forbidden)
    for spoken in ['Ngô Tiểu Đồng','Wu Xiaotong']:
        out=tx('吴小彤')
        issues=await svc._prepare_dubbing_speech(out,profile=LocalizationProfile(id='p',name='p',glossary={'吴小彤':spoken}),
            runtime=runtime(),ai=None,fallback_ai=None,route_state=None,work_dir=tmp_path,progress_cb=None)
        assert out.segments[0].translated_text=='吴小彤'
        assert issues[0]['spoken_text']==spoken


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    [], [{'id':'橙子','text':''}], [{'id':'橙子','text':'橙子'}],
    [{'id':'wrong','text':'Cheng Zi'}], [{'id':'橙子','text':'Cheng Zi'},{'id':'橙子','text':'other'}],
])
async def test_invalid_ai_never_deletes_speech_or_publishes_cache(tmp_path,monkeypatch,payload):
    async def chat(*a,**kw): return json.dumps(payload)
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',chat)
    out=tx('Chị 橙子')
    with pytest.raises(ValueError,match='pronunciation'):
        await svc._prepare_dubbing_speech(out,profile=LocalizationProfile(id='p',name='p'),runtime=runtime(),
            ai=object(),fallback_ai=None,route_state=None,work_dir=tmp_path,progress_cb=None)
    assert out.segments[0].translated_text=='Chị 橙子'
    assert not (tmp_path/'speech_forms.json').exists()


@pytest.mark.asyncio
async def test_chinese_voice_does_not_transliterate(tmp_path,monkeypatch):
    async def forbidden(*a,**kw): pytest.fail('Native script should be preserved')
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',forbidden)
    out=tx()
    issues=await svc._prepare_dubbing_speech(out,profile=LocalizationProfile(id='p',name='p'),runtime=runtime('zh-CN-XiaoxiaoNeural'),
        ai=None,fallback_ai=None,route_state=None,work_dir=tmp_path,progress_cb=None)
    assert issues==[]
    assert out.segments[0].translated_text==tx().segments[0].translated_text


@pytest.mark.asyncio
async def test_tts_wait_does_not_exhaust_unspent_rewrite_budget(tmp_path,monkeypatch):
    import asyncio, wave
    monkeypatch.setattr(svc,'_tts_config',lambda *_: runtime())
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_: [object()])
    monkeypatch.setattr('backend.services.video_utils.probe_duration',lambda *_: 3.0)
    generated=0
    async def generate(text,*,output_path,**kwargs):
        nonlocal generated
        generated+=1
        await asyncio.sleep(0.04)
        duration=5000 if generated==1 else 1000
        with wave.open(str(output_path),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
            w.writeframes(b'\x01\x00'*(duration*16))
        return duration
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    seen=[]
    async def adapt(*args,**kwargs):
        seen.append(kwargs['deadline_at']-asyncio.get_running_loop().time())
        for item in kwargs['plan']: item.spoken_text='shorter'
        return True
    monkeypatch.setattr(svc,'_adapt_chunk_items',adapt)
    profile=LocalizationProfile(id='p',name='p',dub_rewrite_attempts=1,video_retime_mode='off').model_copy(update={'rewrite_total_timeout_s':0.02})
    await svc.synthesize_timed_dub(tx('hello'),source_media=tmp_path/'source.mp4',target_language='Vietnamese',
        profile=profile,user_settings=object(),work_dir=tmp_path/'dub')
    assert len(seen)==1 and seen[0]>0


@pytest.mark.asyncio
async def test_duration_rewrite_cannot_reintroduce_unreadable_names(monkeypatch):
    from backend.services.dubbing_planner import DubbingCue,DubbingConfig,build_plan
    plan,chunks=build_plan([DubbingCue(1,'a',0,1000,'Chị Wu Xiaotong đến đây với em')],DubbingConfig())
    segments={'a':tx('Chị Wu Xiaotong đến đây với em').segments[0]}
    async def chat(*a,**kw): return '[{"id":"a","text":"吴小彤"}]'
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',chat)
    issues=[]
    changed=await svc._adapt_chunk_items(object(),plan=plan,chunk=chunks[0],segment_by_id=segments,
        target_language='Vietnamese',target_ms=1000,profile=LocalizationProfile(id='p',name='p'),issues=issues)
    assert changed is False
    assert plan[0].spoken_text=='Chị Wu Xiaotong đến đây với em'


@pytest.mark.asyncio
async def test_preparation_changes_spoken_audio_but_not_approved_translation(tmp_path,monkeypatch):
    """The user approved 橙子 in the caption text; only TTS needs a spoken form."""
    import wave
    spoken=[]
    monkeypatch.setattr(svc,'_tts_config',lambda *_: runtime())
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_: [object()])
    monkeypatch.setattr('backend.services.video_utils.probe_duration',lambda *_: 3.0)
    async def chat(*a,**kw):
        assert isinstance(kw['messages'],list)
        return '[{"id":"橙子","text":"Cheng Zi"}]'
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',chat)
    async def generate(text,*,output_path,**kwargs):
        spoken.append(text)
        with wave.open(str(output_path),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
            w.writeframes(b'\x01\x00'*16000)
        return 1000
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    original=tx('Chị 橙子 đến đây với em')
    result=await svc.synthesize_timed_dub(original,source_media=tmp_path/'source.mp4',target_language='Vietnamese',
        profile=LocalizationProfile(id='p',name='p',dub_rewrite_attempts=0),user_settings=object(),work_dir=tmp_path/'dub')
    assert spoken==['Chị Cheng Zi đến đây với em']
    # Caption/subtitle text and the stored translation stay exactly as approved.
    assert result.transcript.segments[0].translated_text=='Chị 橙子 đến đây với em'
    assert result.plan is not None
    assert result.plan[0].spoken_text=='Chị Cheng Zi đến đây với em'
    assert svc.caption_segments_from_localization(result.transcript)[0]['text']=='Chị 橙子 đến đây với em'
    assert result.transcript.segments[0].qc_status=='review'


@pytest.mark.asyncio
async def test_rewrite_is_asked_to_shorten_the_spoken_form_not_the_han_text(monkeypatch):
    """A rewrite must see the readable form, or it keeps Han and gets rejected."""
    from backend.services.dubbing_planner import DubbingCue,DubbingConfig,build_plan
    from backend.services.dubbing_speech import spoken_pairs
    plan,chunks=build_plan([DubbingCue(1,'a',0,1000,'Em tên là 大奔小发')],DubbingConfig())
    transcript=tx('Em tên là 大奔小发')
    transcript.metadata['speech_preparation']={'forms':{'大奔小发':'Da Ben Xiao Fa'},'issues':[]}
    pairs=spoken_pairs(transcript,profile=LocalizationProfile(id='p',name='p'),runtime=runtime(),
                       voice_for_chunk=lambda profile,runtime,speaker,override: runtime.default_voice)
    segments={'a':transcript.segments[0]}
    svc._apply_spoken_forms(plan,segments,pairs,'Vietnamese')
    seen={}
    async def chat(*a,**kw):
        seen['prompt']=kw['messages'][0]['content']
        return '[{"id":"a","text":"Tên là Da Ben Xiao Fa"}]'
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',chat)
    changed=await svc._adapt_chunk_items(object(),plan=plan,chunk=chunks[0],segment_by_id=segments,
        target_language='Vietnamese',target_ms=1000,profile=LocalizationProfile(id='p',name='p'),issues=[])
    assert changed is True
    assert '大奔小发' not in seen['prompt']
    assert 'Da Ben Xiao Fa' in seen['prompt']
    assert plan[0].spoken_text=='Tên là Da Ben Xiao Fa'
    # The pipeline re-applies pairs after a rewrite; approved text is restored.
    svc._apply_spoken_forms(plan,segments,pairs,'Vietnamese')
    assert segments['a'].translated_text=='Tên là 大奔小发'
    assert plan[0].spoken_text=='Tên là Da Ben Xiao Fa'
