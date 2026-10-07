"""Auto omission contract: genuine cue-local failures, never fake translation."""
import pytest
from backend.core.localization import LocalizationProfile, LocalizationTranscript, LocalizationSegment
from backend.services import localization_service as svc
from backend.services import localization_qc_service as qc
from tests.test_localization_auto_draft_integration import video


def omitted_tx():
    rows=[LocalizationSegment(id=str(i),source_text='原文',translated_text='Lời dịch.',
        source_start_ms=i*100,source_end_ms=(i+1)*100,translation_status='success',
        target_start_ms=i*100,target_end_ms=(i+1)*100,tts_duration_ms=100) for i in range(10)]
    rows[4]=LocalizationSegment(id='4',source_text='原文',translated_text='',
        source_start_ms=400,source_end_ms=500,translation_status='omitted',qc_status='review')
    return LocalizationTranscript(language='zh',segments=rows,metadata={'target_language':'vi',
        'translation_omissions':{'4':{'source_text':'原文','source_start_ms':400,'source_end_ms':500,
            'rejected_target_text':'Lời 右台','issues':[{'kind':'unlocalized_text'}],
            'attempts':2,'reason':'unreadable_after_bounded_repair'}}})


def test_qc_omission_is_explicit_review_not_missing_or_clean(video):
    tx=omitted_tx()
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='a',name='a',translation_automation='auto',diarization_mode='off'),
        check_tts_quality=True)
    assert report.status=='NEEDS_REVIEW' and not report.failures, report.as_dict()
    warning=next(w for w in report.warnings if w['code']=='translation_omitted')
    assert warning['segment_id']=='4' and warning['rejected_target_text']=='Lời 右台'
    assert report.metrics['omitted_segments']==1


@pytest.mark.parametrize('defect',['strict','missing','stale','unrepaired','empty_candidate','over_budget','all_omitted'])
def test_invalid_omissions_fail_qc(video,defect):
    tx=omitted_tx()
    if defect=='missing':tx.metadata['translation_omissions']={}
    if defect=='stale':tx.metadata['translation_omissions']['4']['source_end_ms']=501
    if defect=='unrepaired':tx.metadata['translation_omissions']['4']['attempts']=1
    if defect=='empty_candidate':tx.metadata['translation_omissions']['4']['rejected_target_text']=''
    if defect=='over_budget':tx.segments.pop()
    if defect=='all_omitted':tx.segments=tx.segments[4:5]
    profile=LocalizationProfile(id='a',name='a',translation_automation='strict' if defect=='strict' else 'auto')
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=profile,check_tts_quality=True)
    assert report.status=='FAILED'


def test_omitted_cue_never_leaks_into_captions():
    tx=omitted_tx()
    tx.segments[4].translated_text='Unexpected stale caption'
    assert len(svc.caption_segments_from_localization(tx))==9


@pytest.mark.asyncio
async def test_c6_omits_only_repaired_unreadable_candidate_and_resumes(monkeypatch,tmp_path):
    import json
    from tests.test_localization_auto_translation import MockAI, _ids, _install
    # Exact recorded source/target that stopped the original second video.
    bad_target='Bé có phần dưới mặt ngắn phù hợp đi theo phong cách右台'
    ai=MockAI(lambda prompt,call:json.dumps([{'id':sid,'text':bad_target if sid=='4' else 'Lời dịch.'}
        for sid in _ids(prompt)],ensure_ascii=False))
    _install(monkeypatch,ai)
    tx=omitted_tx()
    tx.metadata={}
    tx.segments[4].source_text='下庭短的寶寶適合走一些右臺風'
    for s in tx.segments:
        s.translated_text=None;s.translation_status='pending'
        s.target_start_ms=None;s.target_end_ms=None;s.tts_duration_ms=None
    profile=LocalizationProfile(id='a',name='a',translation_automation='auto',alignment_mode='off',diarization_mode='off')
    kwargs=dict(target_language='Vietnamese',profile=profile,user_settings=None,for_dub=True,checkpoint_dir=tmp_path)
    result=await svc.translate_transcript(tx,**kwargs)
    omitted=result.segments[4]
    assert omitted.translation_status=='omitted' and omitted.translated_text==''
    receipt=result.metadata['translation_omissions']['4']
    assert receipt['attempts']==2 and receipt['source_start_ms']==400 and receipt['source_end_ms']==500
    assert len(ai.prompts)>=2 and receipt['rejected_target_text']==bad_target
    count=len(ai.prompts)
    resumed=await svc.translate_transcript(tx,**kwargs)
    assert len(ai.prompts)==count
    assert resumed.metadata['translation_omissions']==result.metadata['translation_omissions']


@pytest.mark.asyncio
async def test_real_ffmpeg_omission_gap_has_no_speech_and_no_shift(monkeypatch,tmp_path):
    import wave
    from tests.test_localization_postfit_measurement import audio,runtime,source_probe
    tx=omitted_tx()
    for s in tx.segments:
        s.source_start_ms*=10;s.source_end_ms*=10
        if s.translation_status!='omitted':
            s.target_start_ms=None;s.target_end_ms=None;s.tts_duration_ms=None
    tx.metadata['translation_omissions']['4'].update(source_start_ms=4000,source_end_ms=5000)
    async def generate(text,*,output_path,**kwargs):
        audio(output_path,700)
        return 700
    monkeypatch.setattr(svc,'_generate_tts_chunk',generate)
    monkeypatch.setattr(svc,'_tts_config',lambda *_:runtime())
    monkeypatch.setattr('backend.services.video_utils.probe_duration',source_probe(10.0))
    profile=LocalizationProfile(id='a',name='a',translation_automation='auto',dub_rewrite_attempts=0,video_retime_mode='off')
    result=await svc.synthesize_timed_dub(tx,source_media=tmp_path/'source.mp4',target_language='Vietnamese',
        profile=profile,user_settings=object(),work_dir=tmp_path/'work')
    for row in result.transcript.metadata['dubbing']['fitted_audio']:
        assert row['end_ms']<=4000 or row['start_ms']>=5000, row
    assert result.transcript.segments[5].target_start_ms>=5000
    assert result.transcript.segments[4].tts_duration_ms is None
    with wave.open(str(result.dub_track),'rb') as f:
        f.setpos(f.getframerate()*4)
        assert not any(f.readframes(f.getframerate()))
    assert len(svc.caption_segments_from_localization(result.transcript))==9


@pytest.mark.asyncio
async def test_c7_invalid_omission_fails_before_any_provider(monkeypatch,tmp_path):
    tx=omitted_tx();tx.metadata['translation_omissions']={}
    def forbidden(*args,**kwargs):pytest.fail('Invalid omission reached provider resolution')
    monkeypatch.setattr(svc,'_tts_config',forbidden)
    with pytest.raises(ValueError,match='omission audit'):
        await svc.synthesize_timed_dub(tx,source_media=tmp_path/'source.mp4',target_language='Vietnamese',
            profile=LocalizationProfile(id='a',name='a',translation_automation='auto'),
            user_settings=object(),work_dir=tmp_path/'work')
