"""Actual PCM cadence regression; only remote TTS leaf is a test double."""
import asyncio
import hashlib
import json
import math
import struct
import wave
from pathlib import Path
import pytest
from backend.core.localization import LocalizationProfile,LocalizationSegment,LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider


def energy(path,start_ms,end_ms):
    with wave.open(str(path),'rb') as w:
        rate=w.getframerate();channels=w.getnchannels();assert w.getsampwidth()==2
        w.setpos(round(rate*start_ms/1000));data=w.readframes(round(rate*(end_ms-start_ms)/1000))
    values=struct.unpack('<'+'h'*(len(data)//2),data)
    return math.sqrt(sum(v*v for v in values)/max(1,len(values)))

@pytest.mark.parametrize('shift,slowdown',[(0,1.0),(1300,1.0)])
def test_measured_word_cadence_preserves_source_pause_without_resynthesizing_cues(tmp_path,monkeypatch,shift,slowdown):
    tx=LocalizationTranscript(language='zh',segments=[LocalizationSegment(
        id=f's{i}',source_text='source',translated_text=text,translation_status='success',source_start_ms=shift+i*3000,source_end_ms=shift+(i+1)*3000)
        for i,text in enumerate(['Xin chào','Cảm ơn'])])
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_: [])
    monkeypatch.setattr(svc,'_tts_config',lambda *_:svc.TTSRuntimeConfig(TTSProvider.EDGE_TTS,'vi-VN-HoaiMyNeural','',None,None,None))
    monkeypatch.setattr('backend.services.video_utils.probe_duration',lambda *_:10.0)
    calls=[]
    async def generate(text,*,output_path,**kwargs):
        calls.append(text)
        rate=16000;data=struct.pack('<'+'h'*25600,*[round(7000*math.sin(2*math.pi*220*i/rate)) for i in range(25600)])
        with wave.open(str(output_path),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(data)
        metadata={'version':1,'text':text,'processed_text':text,'voice':'vi-VN-HoaiMyNeural','audio_sha256':hashlib.sha256(output_path.read_bytes()).hexdigest(),'timing_basis':'provider_word_boundary','words':[{'word':word,'start_ms':i*400,'end_ms':(i+1)*400} for i,word in enumerate(text.split())]}
        output_path.with_suffix(output_path.suffix+'.words.json').write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
        return 1600
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    result=asyncio.run(svc.synthesize_timed_dub(tx,source_media=tmp_path/'source.mp4',target_language='Vietnamese',profile=LocalizationProfile(id='p',name='P',video_retime_mode='off',dub_rewrite_attempts=0),user_settings=None,work_dir=tmp_path/'dub'))
    assert calls==['Xin chào Cảm ơn'],'Cadence correction must reuse the measured chunk, not require separate remote synthesis'
    assert [p.target_start_ms for p in result.plan]==[shift,shift+3000]
    assert energy(result.dub_track,shift+100,shift+600)>1000
    assert energy(result.dub_track,shift+1000,shift+2800)<5
    assert energy(result.dub_track,shift+3100,shift+3600)>1000
    assert not any(i['reason']=='estimated_onset_drift' for i in result.issues)
    assert result.transcript.metadata['dubbing']['cue_timing_basis']=='measured_word_boundaries_with_source_anchor_placement'
    assert [s.source_start_ms for s in tx.segments]==[shift,shift+3000]
    assert max(p.speed_factor for p in result.plan)<=1.30


def test_boundary_trim_keeps_identity_bound_word_timing_on_the_trimmed_audio(tmp_path):
    from backend.services.dubbing_cadence import read_cue_spans
    audio=tmp_path/'edge.wav'
    rate=16000
    samples=[0]*3200+[round(7000*math.sin(2*math.pi*220*i/rate)) for i in range(12800)]+[0]*4800
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
        w.writeframes(struct.pack('<'+'h'*len(samples),*samples))
    side=audio.with_suffix(audio.suffix+'.words.json')
    before={'version':1,'text':'Xin chào','processed_text':'Xin chào','voice':'v','audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'timing_basis':'provider_word_boundary','words':[{'word':'Xin','start_ms':200,'end_ms':600},{'word':'chào','start_ms':600,'end_ms':1000}]}
    side.write_text(json.dumps(before,ensure_ascii=False),encoding='utf-8')
    duration=svc._trim_edge_tts_boundary_silence_sync(audio,1300)
    assert duration<1300
    spans=read_cue_spans(audio,['Xin chào'],duration)
    assert spans is not None,'Valid native boundaries were lost after production boundary trim'
    after=json.loads(side.read_text(encoding='utf-8'))
    assert after['audio_sha256']==hashlib.sha256(audio.read_bytes()).hexdigest()!=before['audio_sha256']
    assert after['words'][0]['start_ms']<200
    assert after['words'][1]['end_ms']<=duration
    assert after['text']==before['text']
