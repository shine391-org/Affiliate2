"""Real media boundary regressions; provider fixtures are not live TTS."""
import array
import math
import wave

from pathlib import Path

import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider
from backend.services.video_utils import probe_duration as real_probe_duration



def source_probe(seconds):
    return lambda path, *args: seconds if Path(path).name == 'source.mp4' else real_probe_duration(path, *args)


def audio(path, duration=1000, silent=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate=16000
    samples=array.array('h',(0 if silent else round(3000*math.sin(2*math.pi*440*n/rate)) for n in range(rate*duration//1000)))
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(samples.tobytes())
    return path


def runtime():
    return svc.TTSRuntimeConfig(provider=TTSProvider.OPENAI_COMPATIBLE,default_voice='qa',api_key='',model=None,base_url=None,region=None)


@pytest.mark.asyncio
async def test_multi_cue_chunk_without_events_keeps_unverified_cadence_warning(tmp_path, monkeypatch):
    async def generate(text, *, output_path, **kwargs):
        audio(output_path, 1400)
        return 1400
    monkeypatch.setattr(svc, '_generate_tts_chunk', generate)
    monkeypatch.setattr(svc, '_tts_config', lambda *_: runtime())
    monkeypatch.setattr('backend.services.video_utils.probe_duration', source_probe(2.0))
    tx = LocalizationTranscript(segments=[LocalizationSegment(
        id=str(i), source_text=text, translated_text=text, source_start_ms=i*800,
        source_end_ms=(i+1)*800, translation_status='success')
        for i, text in enumerate(('Xin chào', 'bạn'))])
    profile = LocalizationProfile(id='p', name='P', dub_rewrite_attempts=0, video_retime_mode='off')
    result = await svc.synthesize_timed_dub(tx, source_media=tmp_path/'source.mp4',
        target_language='Vietnamese', profile=profile, user_settings=object(), work_dir=tmp_path/'work')
    issue = next(i for i in result.issues if i['reason'] == 'multi_cue_unverified_timing')
    assert issue['segment_ids'] == ['0', '1']
    assert all(s.tts_duration_basis == 'allocated' and s.qc_status == 'review'
               for s in result.transcript.segments)


@pytest.mark.asyncio
@pytest.mark.parametrize('duration', [600, 1603, 2000])
@pytest.mark.parametrize('speed', [1.0, 1.0005])
async def test_transformed_audio_is_probed_even_at_natural_speed(tmp_path, duration, speed):
    """Rate alone cannot prove cadence trim/mix/delay left raw bytes intact."""
    path = audio(tmp_path / 'cadence.wav', duration)
    measured, peak = await svc._measure_fitted_audio(path, 1600, speed)
    assert measured == duration
    assert math.isfinite(peak)


@pytest.mark.asyncio
@pytest.mark.parametrize('policy',['strict','auto'])
async def test_auto_postfit_can_correct_tiny_delta_at_regular_speed_ceiling(tmp_path,monkeypatch,policy):
    async def generate(text,*,output_path,**kwargs):
        audio(output_path,2000)
        return 2000
    real_fit=svc._fit_chunk_audio_sync
    calls=[]
    def fit(source,destination,speed):
        calls.append(speed)
        if len(calls)==1:
            # Fault injection: a measured 3ms processing delta at the normal
            # ceiling. Subsequent correction uses real FFmpeg from raw PCM.
            return audio(destination,1603)
        return real_fit(source,destination,speed)
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    monkeypatch.setattr(svc,'_fit_chunk_audio_sync',fit)
    monkeypatch.setattr(svc,'_tts_config',lambda *_:runtime())
    monkeypatch.setattr('backend.services.video_utils.probe_duration',source_probe(1.6))
    tx=LocalizationTranscript(segments=[LocalizationSegment(id='s1',source_text='source',
        translated_text='Lời dịch.',source_start_ms=0,source_end_ms=1600,translation_status='success',
        overrides={'target_start_ms':0,'target_end_ms':1600})])
    profile=LocalizationProfile(id='qa',name='qa',translation_automation=policy,
        dub_speed_max=1.25,dub_emergency_speed_max=1.35,dub_rewrite_attempts=0,video_retime_mode='off')
    kwargs=dict(source_media=tmp_path/'source.mp4',target_language='Vietnamese',profile=profile,
        user_settings=object(),work_dir=tmp_path/'work')
    if policy=='strict':
        with pytest.raises(svc.LocalizationTimingError):
            await svc.synthesize_timed_dub(tx,**kwargs)
    else:
        result=await svc.synthesize_timed_dub(tx,**kwargs)
        assert any(x['reason']=='postfit_micro_rescue' for x in result.issues)
        measurement=result.transcript.metadata['dubbing']['fitted_audio'][0]
        assert measurement['end_ms']<=1600
        assert measurement['measured_fitted_duration_ms']==await svc._audio_duration_ms(Path(measurement['path']))
        assert 1.25 < calls[-1] <= 1.25*1.01


@pytest.mark.parametrize('same_size',[False,True])
def test_chunk_identity_detects_changed_output_bytes(tmp_path,same_size):
    path=audio(tmp_path/'raw.wav')
    svc._write_chunk_identity(path,'test-binding',1000)
    assert svc._chunk_identity_matches(path,'test-binding')
    data=path.read_bytes()
    path.write_bytes(data[:-16] if not same_size else data[:-16]+b'\x00'*16)
    assert not svc._chunk_identity_matches(path,'test-binding')
    assert svc._chunk_persisted_duration_ms(path,'test-binding') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['long_fitted_tail','silent_audio'])
async def test_pipeline_rejects_bad_fitted_audio_before_composition(tmp_path,monkeypatch,failure):
    async def generate(text,*,output_path,**kwargs):
        # 2s raw requires real atempo fitting into a 1.8s locked source window.
        audio(output_path,2000)
        return 2000
    def bad_fit(source,destination,speed):
        return audio(destination,2600 if failure=='long_fitted_tail' else 1700,silent=failure=='silent_audio')
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    monkeypatch.setattr(svc,'_fit_chunk_audio_sync',bad_fit)
    monkeypatch.setattr(svc,'_tts_config',lambda *_:runtime())
    monkeypatch.setattr('backend.services.video_utils.probe_duration',source_probe(1.8))
    def forbidden(*args,**kwargs):
        pytest.fail('Invalid speech must be rejected BEFORE compose/atrim can hide it')
    monkeypatch.setattr(svc,'_compose_chunk_track_sync',forbidden)
    tx=LocalizationTranscript(segments=[LocalizationSegment(id='s1',source_text='原文',translated_text='Lời dịch.',source_start_ms=0,source_end_ms=1800,translation_status='success',overrides={'target_start_ms':0,'target_end_ms':1800})])
    profile=LocalizationProfile(id='qa',name='qa',dub_rewrite_attempts=0,video_retime_mode='off')
    with pytest.raises(svc.LocalizationTimingError):
        await svc.synthesize_timed_dub(tx,source_media=tmp_path/'source.mp4',target_language='Vietnamese',profile=profile,user_settings=object(),work_dir=tmp_path/'work')


@pytest.mark.asyncio
async def test_postfit_duration_is_measured_and_persisted(tmp_path,monkeypatch):
    async def generate(text,*,output_path,**kwargs):
        audio(output_path,1900)
        return 1900
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    monkeypatch.setattr(svc,'_tts_config',lambda *_:runtime())
    monkeypatch.setattr('backend.services.video_utils.probe_duration',source_probe(2.0))
    tx=LocalizationTranscript(segments=[LocalizationSegment(id='s1',source_text='source',translated_text='Lời dịch.',source_start_ms=0,source_end_ms=1800,translation_status='success',overrides={'target_start_ms':0,'target_end_ms':1800})])
    result=await svc.synthesize_timed_dub(tx,source_media=tmp_path/'source.mp4',target_language='Vietnamese',profile=LocalizationProfile(id='qa',name='qa',dub_rewrite_attempts=0,video_retime_mode='off'),user_settings=object(),work_dir=tmp_path/'work')
    measurements=result.transcript.metadata['dubbing']['fitted_audio']
    assert len(measurements)==1
    m=measurements[0]
    assert m['raw_duration_ms']==1900
    assert m['measured_fitted_duration_ms']==await svc._audio_duration_ms(Path(m['path']))
    assert m['duration_basis']=='audio_probe'
    dubbing=result.transcript.metadata['dubbing']
    # One unaligned single-cue chunk: the chunk audio is measured, but the cue
    # interval itself is an allocated proportional estimate, never individual
    # measured synthesis. The aggregate must say so.
    assert dubbing['segment_tts_duration_basis']=='allocated'
    assert dubbing['segment_duration_basis']=={'s1':'allocated'}
    assert dubbing['measured_segment_ids']==[] and dubbing['allocated_segment_ids']==['s1']
    assert result.transcript.segments[0].tts_duration_basis=='allocated'


@pytest.mark.asyncio
async def test_mixed_measured_and_allocated_cues_report_mixed_basis(tmp_path,monkeypatch):
    """A measured word-boundary chunk plus an allocated single-cue chunk must
    report the job as MIXED, not overclaim the whole job as measured."""
    import hashlib, json
    tx=LocalizationTranscript(segments=[
        LocalizationSegment(id='s1',source_text='source',translated_text='Xin chào',
            source_start_ms=0,source_end_ms=3000,translation_status='success'),
        LocalizationSegment(id='s2',source_text='source',translated_text='Cảm ơn',
            source_start_ms=3000,source_end_ms=6000,translation_status='success'),
        LocalizationSegment(id='s3',source_text='source',translated_text='Tạm biệt.',
            source_start_ms=9000,source_end_ms=12000,translation_status='success')])
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_: (_ for _ in ()).throw(RuntimeError('no AI')))
    monkeypatch.setattr(svc,'_tts_config',lambda *_:runtime())
    monkeypatch.setattr('backend.services.video_utils.probe_duration',source_probe(13.0))
    async def generate(text,*,output_path,**kwargs):
        if 'Xin' in text:
            audio(output_path,1600)
            side=Path(output_path).with_suffix(Path(output_path).suffix+'.words.json')
            side.write_text(json.dumps({'version':1,'text':text,'processed_text':text,'voice':'qa',
                'audio_sha256':hashlib.sha256(Path(output_path).read_bytes()).hexdigest(),
                'timing_basis':'provider_word_boundary',
                'words':[{'word':w,'start_ms':i*400,'end_ms':(i+1)*400}
                         for i,w in enumerate(text.split())]},ensure_ascii=False),encoding='utf-8')
            return 1600
        audio(output_path,600)
        return 600
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    profile=LocalizationProfile(id='qa',name='qa',dub_rewrite_attempts=0,video_retime_mode='off')
    result=await svc.synthesize_timed_dub(tx,source_media=tmp_path/'source.mp4',
        target_language='Vietnamese',profile=profile,user_settings=object(),work_dir=tmp_path/'work')
    dubbing=result.transcript.metadata['dubbing']
    assert dubbing['segment_tts_duration_basis']=='mixed'
    assert dubbing['segment_duration_basis']=={'s1':'measured','s2':'measured','s3':'allocated'}
    assert dubbing['measured_segment_ids']==['s1','s2']
    assert dubbing['allocated_segment_ids']==['s3']
    assert dubbing['aligned_segment_ids']==['s1','s2']
    assert dubbing['cue_timing_basis']=='mixed_measured_word_boundaries_and_estimated_chunk_internal_offsets'
    by={s.id:s for s in result.transcript.segments}
    assert by['s1'].tts_duration_basis=='measured' and by['s2'].tts_duration_basis=='measured'
    assert by['s3'].tts_duration_basis=='allocated'


@pytest.mark.asyncio
async def test_tts_never_return_is_deadlined_and_cancelled(tmp_path,monkeypatch):
    import asyncio
    started=asyncio.Event();cancelled=asyncio.Event()
    async def never(*args,**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    monkeypatch.setattr('backend.services.tts_service.generate_tts',never)
    with pytest.raises((TimeoutError,RuntimeError),match='timeout|deadline|timed out'):
        await asyncio.wait_for(svc._generate_tts_chunk('Lời dịch',runtime=runtime(),voice='qa',profile=LocalizationProfile(id='qa',name='qa'),speaker_id=None,target_language='Vietnamese',output_path=tmp_path/'raw.wav',attempts=1,request_timeout_s=0.03),timeout=1)
    assert started.is_set() and cancelled.is_set()


@pytest.mark.asyncio
async def test_cancellation_stops_tts_before_network_dispatch(tmp_path,monkeypatch):
    from backend.core.exceptions import JobCancelledError
    async def cancel(): return True
    async def forbidden(*args,**kwargs): pytest.fail('No dispatch after cancellation')
    monkeypatch.setattr('backend.services.tts_service.generate_tts',forbidden)
    with pytest.raises(JobCancelledError):
        await svc._generate_tts_chunk('Lời dịch',runtime=runtime(),voice='qa',profile=LocalizationProfile(id='qa',name='qa'),speaker_id=None,target_language='Vietnamese',output_path=tmp_path/'raw.wav',cancel_check=cancel)


@pytest.mark.asyncio
@pytest.mark.parametrize('policy',['strict','auto'])
@pytest.mark.parametrize('partial_failure',[False,True])
async def test_edge_noaudio_verbatim_split_is_bounded_atomic_and_measured(tmp_path,monkeypatch,partial_failure,policy):
    from edge_tts.exceptions import NoAudioReceived
    from backend.core.exceptions import VoiceGenerationError
    text='xanh xanh dáng vẻ cô gái trẻ ấy Chúng tôi đến đài phát thanh truyền hình cũng học nhiếp ảnh Nhận đi, nhìn bên phải'
    calls=[]
    async def generate(piece,*,output_path,**kwargs):
        calls.append(piece)
        if piece==text or (partial_failure and len(calls)==5):
            raise VoiceGenerationError('No audio') from NoAudioReceived('No audio was received')
        return audio(output_path,700)

    # Avoid spending wall time on retry backoff; audio concat/probe are REAL.
    async def sleep(*args):pass
    monkeypatch.setattr('backend.services.tts_service.generate_tts',generate)
    monkeypatch.setattr(svc.asyncio,'sleep',sleep)
    cfg=svc.TTSRuntimeConfig(provider=TTSProvider.EDGE_TTS,default_voice='vi-VN-HoaiMyNeural',api_key='',model=None,base_url=None,region=None)
    out=tmp_path/'raw.wav';events=[]
    kwargs=dict(runtime=cfg,voice=cfg.default_voice,profile=LocalizationProfile(id='qa',name='qa',translation_automation=policy),
        speaker_id=None,target_language='Vietnamese',output_path=out,recovery_events=events)
    if partial_failure:
        with pytest.raises(RuntimeError):await svc._generate_tts_chunk(text,**kwargs)
        assert not out.exists() and events==[]
    else:
        measured=await svc._generate_tts_chunk(text,**kwargs)
        assert 0<measured==await svc._audio_duration_ms(out)
        assert events[0]['text']==text and ' '.join(events[0]['pieces'])==text
        assert events[0]['reason']=='tts_transport_split'
    assert len(calls)==5 and ' '.join(calls[-2:])==text


@pytest.mark.asyncio
async def test_generic_empty_edge_failure_never_splits_or_omits(tmp_path,monkeypatch):
    calls=[]
    async def empty(text,*,output_path,**kwargs):
        calls.append(text);output_path.write_bytes(b'')
        return output_path
    async def sleep(*args):pass
    monkeypatch.setattr('backend.services.tts_service.generate_tts',empty)
    monkeypatch.setattr(svc.asyncio,'sleep',sleep)
    cfg=svc.TTSRuntimeConfig(provider=TTSProvider.EDGE_TTS,default_voice='qa',api_key='',model=None,base_url=None,region=None)
    with pytest.raises(RuntimeError):
        await svc._generate_tts_chunk('Lời dịch đầy đủ',runtime=cfg,voice='qa',
            profile=LocalizationProfile(id='qa',name='qa'),speaker_id=None,target_language='Vietnamese',output_path=tmp_path/'raw.wav')
    assert len(calls)==3 and not (tmp_path/'raw.wav').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('policy',['strict','auto'])
async def test_edge_split_obeys_parent_deadline(tmp_path,monkeypatch,policy):
    import asyncio
    from edge_tts.exceptions import NoAudioReceived
    from backend.core.exceptions import VoiceGenerationError
    calls=[]
    async def failed(text,**kwargs):
        calls.append(text)
        raise VoiceGenerationError('No audio') from NoAudioReceived('No audio was received')
    async def sleep(*args):pass
    monkeypatch.setattr('backend.services.tts_service.generate_tts',failed)
    monkeypatch.setattr(svc.asyncio,'sleep',sleep)
    cfg=svc.TTSRuntimeConfig(provider=TTSProvider.EDGE_TTS,default_voice='qa',api_key='',model=None,base_url=None,region=None)
    with pytest.raises(RuntimeError,match='TTS chunk failed'):
        await svc._generate_tts_chunk('Lời dịch đầy đủ',runtime=cfg,voice='qa',
            profile=LocalizationProfile(id='qa',name='qa',translation_automation=policy),
            speaker_id=None,target_language='Vietnamese',output_path=tmp_path/'raw.wav',
            _deadline_at=asyncio.get_running_loop().time()+20)
    assert len(calls)==3


@pytest.mark.asyncio
@pytest.mark.parametrize('policy',['strict','auto'])
async def test_c7_transport_recovery_audit_survives_raw_reuse_and_requires_review(tmp_path,monkeypatch,policy):
    import json
    from edge_tts.exceptions import NoAudioReceived
    from backend.core.exceptions import VoiceGenerationError
    from backend.services import localization_qc_service as qc
    text='xanh xanh dáng vẻ cô gái trẻ ấy Chúng tôi đến đài phát thanh truyền hình cũng học nhiếp ảnh Nhận đi, nhìn bên phải'
    calls=[]
    async def generate(piece,*,output_path,**kwargs):
        calls.append(piece)
        if piece==text:
            raise VoiceGenerationError('No audio') from NoAudioReceived('No audio was received')
        return audio(output_path,700)
    async def sleep(*args):pass
    monkeypatch.setattr('backend.services.tts_service.generate_tts',generate)
    monkeypatch.setattr(svc.asyncio,'sleep',sleep)
    cfg=svc.TTSRuntimeConfig(provider=TTSProvider.EDGE_TTS,default_voice='vi-VN-HoaiMyNeural',api_key='',model=None,base_url=None,region=None)
    monkeypatch.setattr(svc,'_tts_config',lambda *_:cfg)
    monkeypatch.setattr('backend.services.video_utils.probe_duration',source_probe(1.8))
    tx=LocalizationTranscript(segments=[LocalizationSegment(id='s1',source_text='原文',translated_text=text,
        source_start_ms=0,source_end_ms=1500,translation_status='success')])
    profile=LocalizationProfile(id='qa',name='qa',translation_automation=policy,dub_rewrite_attempts=0,video_retime_mode='off',target_aspect='source')
    kwargs=dict(source_media=tmp_path/'source.mp4',target_language='Vietnamese',profile=profile,
        user_settings=object(),work_dir=tmp_path/'work')
    result=await svc.synthesize_timed_dub(tx,**kwargs)
    recovery=next(x for x in result.issues if x['reason']=='tts_transport_split')
    assert ' '.join(recovery['pieces'])==text and recovery['segment_ids']==['s1']
    state=json.loads((tmp_path/'work/dubbing_state.json').read_text())
    assert state['tts_recoveries']['1']['text']==text
    call_count=len(calls)
    resumed=await svc.synthesize_timed_dub(tx,reuse_existing_chunks=True,**kwargs)
    assert len(calls)==call_count
    assert any(x['reason']=='tts_transport_split' for x in resumed.issues)
    import subprocess
    video=tmp_path/'qc-video.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=black:s=64x96:d=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-threads','1','-c:a','aac',str(video)],
        check=True,capture_output=True,timeout=30)
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=resumed.transcript,
        profile=profile,timing_issues=resumed.issues,check_tts_quality=True)
    assert report.status=='NEEDS_REVIEW' and not report.failures,report.as_dict()
    assert any(x['code']=='tts_transport_split' for x in report.warnings)
    from backend.services.localization_publish_service import _publishable_qc
    assert not _publishable_qc({'qc_status':report.status,'qc_human_approved':False})

