"""LOCAL generated-media QA: real FFmpeg/ffprobe/QC, no AI provider calls."""
import subprocess
import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_qc_service as qc
from backend.services import dubbing_speech
from tests.test_localization_postfit_measurement import audio, runtime


@pytest.fixture
def video(tmp_path):
    wav = audio(tmp_path/'tone.wav', 2000)
    path = tmp_path/'fixture.mp4'
    result = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','color=c=black:s=160x284:r=10:d=2','-i',str(wav),
        '-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],
        capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    return path


def draft_tx():
    segment = LocalizationSegment(id='d',source_text='三公斤',translated_text='4 kg.',
        source_start_ms=0,source_end_ms=1000,target_start_ms=0,target_end_ms=1000,
        translation_status='draft',timing_status='fit',qc_status='pending')
    record = {'source_text':segment.source_text,'target_text':segment.translated_text,
        'issues':[{'kind':'quantity_lost','severity':'critical'}],'attempts':[{'attempt':1,'accepted':False}]}
    return LocalizationTranscript(language='zh',segments=[segment],metadata={
        'target_language':'vi','translation_auto_drafts':{'d':record}})


@pytest.mark.parametrize('quality',['strict','standard'])
def test_real_qc_audited_draft_is_review_not_failed_or_ready(video,quality):
    report = qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=draft_tx(),
        profile=LocalizationProfile(id='auto',name='Auto',translation_automation='auto',qc_profile=quality,
            diarization_mode='off'),check_tts_quality=False)
    assert report.status == 'NEEDS_REVIEW', report.as_dict()
    assert not report.failures
    warning = next(x for x in report.warnings if x['code']=='translation_auto_draft')
    assert warning['segment_id']=='d'
    assert warning['source_text']=='三公斤' and warning['target_text']=='4 kg.'
    assert warning['issues'][0]['kind']=='quantity_lost'
    assert warning['source_start_ms']==0 and warning['source_end_ms']==1000
    assert report.metrics['draft_segments']==1


@pytest.mark.parametrize('mode',['strict','missing','stale_source','stale_target','empty_issues','empty_target'])
def test_real_qc_invalid_or_strict_draft_remains_failed(video,mode):
    tx = draft_tx()
    if mode=='missing': tx.metadata['translation_auto_drafts']={}
    if mode=='stale_source': tx.segments[0].source_text='五公斤'
    if mode=='stale_target': tx.segments[0].translated_text='5 kg.'
    if mode=='empty_issues': tx.metadata['translation_auto_drafts']['d']['issues']=[]
    if mode=='empty_target':
        tx.segments[0].translated_text=' '
        tx.metadata['translation_auto_drafts']['d']['target_text']=' '
    report = qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='qa',name='QA',translation_automation='strict' if mode=='strict' else 'auto',
            diarization_mode='off'),check_tts_quality=False)
    assert report.status=='FAILED'
    assert any(x['code']=='untranslated_segments' for x in report.failures)


@pytest.mark.asyncio
async def test_auto_draft_does_not_bypass_foreign_script_speech_validation():
    tx = draft_tx()
    tx.segments[0].translated_text='这是中文原文'
    tx.metadata['translation_auto_drafts']['d']['target_text']=tx.segments[0].translated_text
    with pytest.raises(ValueError,match='Unlocalized Vietnamese text'):
        await dubbing_speech.prepare_speech(tx,profile=LocalizationProfile(id='auto',name='Auto',
            translation_automation='auto'),runtime=runtime())


@pytest.mark.parametrize('ratio',[0.50,0.60,0.67,0.719])
@pytest.mark.parametrize('policy',['strict','auto'])
def test_real_qc_moderate_duration_coverage_is_review_only_in_auto(video,ratio,policy):
    tx=draft_tx()
    tx.segments[0].translation_status='success'
    tx.segments[0].translated_text='3 kg.'
    tx.segments[0].target_end_ms=round(1000*ratio)
    tx.segments[0].tts_duration_ms=round(1000*ratio)
    tx.segments[0].tts_duration_basis='measured'
    tx.metadata.pop('translation_auto_drafts')
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='qa',name='QA',translation_automation=policy,
            diarization_mode='off'),check_tts_quality=True)
    if policy=='auto':
        assert report.status=='NEEDS_REVIEW'
        assert not any(x['code']=='low_target_speech_coverage' for x in report.failures)
        warning=next(x for x in report.warnings if x['code']=='low_target_speech_coverage')
        assert warning['ratio']==round(ratio,4)
        assert warning['strict_min_ratio']==0.72 and warning['auto_min_ratio']==0.50
        assert warning['requires_human_review'] is True
    else:
        assert report.status=='FAILED'
        assert any(x['code']=='low_target_speech_coverage' for x in report.failures)


@pytest.mark.parametrize('defect',['severe','empty_target','untranslated','missing_tts'])
def test_auto_coverage_does_not_hide_missing_or_severely_short_speech(video,defect):
    tx=draft_tx()
    tx.segments[0].translation_status='success'
    tx.segments[0].translated_text='3 kg.'
    tx.segments[0].target_end_ms=490 if defect=='severe' else 670
    tx.segments[0].tts_duration_ms=670
    tx.metadata.pop('translation_auto_drafts')
    if defect=='empty_target':tx.segments[0].translated_text=' '
    if defect=='untranslated':tx.segments[0].translation_status='untranslated'
    if defect=='missing_tts':tx.segments[0].tts_duration_ms=None
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='qa',name='QA',translation_automation='auto',
            diarization_mode='off'),check_tts_quality=True)
    assert report.status=='FAILED'
    assert report.failures


def _success_tx(*rows):
    return LocalizationTranscript(language='zh',segments=list(rows),metadata={'target_language':'vi'})


def _seg(seg_id,start,end,target_start,target_end,tts,basis):
    return LocalizationSegment(id=seg_id,source_text='原文',translated_text='Lời dịch.',
        source_start_ms=start,source_end_ms=end,target_start_ms=target_start,target_end_ms=target_end,
        tts_duration_ms=tts,translation_status='success',tts_duration_basis=basis)


@pytest.mark.parametrize('mode',['measured','single_cue_audio'])
def test_real_qc_moderate_coverage_relaxation_requires_positive_measured_evidence(video,mode):
    seg=_seg('s1',0,1000,0,600,600,'measured' if mode=='measured' else 'allocated')
    tx=_success_tx(seg)
    if mode=='measured':
        tx.metadata['dubbing']={'segment_tts_duration_basis':'measured',
            'segment_duration_basis':{'s1':'measured'}}
    else:
        tx.metadata['dubbing']={'segment_tts_duration_basis':'allocated',
            'fitted_audio':[{'segment_ids':['s1'],'duration_basis':'audio_probe',
                'measured_fitted_duration_ms':600,'start_ms':0,'end_ms':600}]}
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='qa',name='QA',translation_automation='auto',
            diarization_mode='off'),check_tts_quality=True)
    assert report.status=='NEEDS_REVIEW',report.as_dict()
    warning=next(x for x in report.warnings if x['code']=='low_target_speech_coverage')
    assert warning['requires_human_review'] is True and warning['auto_min_ratio']==0.50
    assert not any(x['code']=='low_target_speech_coverage' for x in report.failures)


@pytest.mark.parametrize('mode',['allocated','aggregate_measured_but_allocated','aggregate_only',
    'legacy_no_basis','conflicting_map','shared_chunk','empty_probe','stale_probe'])
def test_real_qc_moderate_coverage_relaxation_fails_closed_without_cue_measured_evidence(video,mode):
    basis=None if mode in {'aggregate_only','legacy_no_basis'} else 'allocated'
    seg=_seg('s1',0,1000,0,600,600,basis)
    tx=_success_tx(seg)
    tx.metadata['dubbing']={'segment_tts_duration_basis':(
        'allocated' if mode=='allocated' else 'measured'),
        'segment_duration_basis':{} if mode=='aggregate_only' else {'s1':basis}}
    if mode=='legacy_no_basis':tx.metadata.pop('dubbing')
    if mode=='conflicting_map':tx.metadata['dubbing']['segment_duration_basis']={'s1':'measured'}
    if mode in {'shared_chunk','empty_probe','stale_probe'}:
        tx.metadata['dubbing']['fitted_audio']=[{
            'segment_ids':['s1','other'] if mode=='shared_chunk' else ['s1'],
            'duration_basis':'audio_probe','measured_fitted_duration_ms':0 if mode=='empty_probe' else 600,
            'start_ms':1000 if mode=='stale_probe' else 0,'end_ms':1600 if mode=='stale_probe' else 600}]
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='qa',name='QA',translation_automation='auto',
            diarization_mode='off'),check_tts_quality=True)
    assert report.status=='FAILED',report.as_dict()
    assert any(x['code']=='low_target_speech_coverage' for x in report.failures)


@pytest.mark.parametrize('mixed_basis',['measured','allocated'])
def test_real_qc_mixed_measured_and_allocated_cues_never_relax_on_partial_evidence(video,mixed_basis):
    # Two cues give the same 0.60 coverage; only the per-cue basis differs.
    tx=_success_tx(_seg('s1',0,1000,0,600,600,'measured'),
                   _seg('s2',1000,2000,1000,1600,600,mixed_basis))
    tx.metadata['dubbing']={'segment_tts_duration_basis':'mixed',
        'segment_duration_basis':{'s1':'measured','s2':mixed_basis}}
    report=qc.evaluate_localization_qc(source_video=video,final_video=video,transcript=tx,
        profile=LocalizationProfile(id='qa',name='QA',translation_automation='auto',
            diarization_mode='off'),check_tts_quality=True)
    if mixed_basis=='measured':
        assert report.status=='NEEDS_REVIEW',report.as_dict()
        assert not any(x['code']=='low_target_speech_coverage' for x in report.failures)
    else:
        assert report.status=='FAILED',report.as_dict()
        assert any(x['code']=='low_target_speech_coverage' for x in report.failures)

