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
    assert result.transcript.metadata['dubbing']['segment_tts_duration_basis']=='allocated_interval_not_individual_synthesis'


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
