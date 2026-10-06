from contextlib import asynccontextmanager
import pytest
from backend.core.localization import LocalizationProfile
from backend.services import localization_service as svc
from backend.services.whisper_service import whisper_service


@pytest.mark.asyncio
@pytest.mark.parametrize('device,compute,fallback',[('cuda','float16',None),('cpu','int8','CUDA runtime unavailable')])
async def test_localization_persists_executor_result_not_requested_asr(device,compute,fallback,monkeypatch,tmp_path):
    provenance={'model':'small','device':device,'compute_type':compute,'fallback_reason':fallback,
                'requested_device':'cuda','requested_compute_type':'float16'}
    async def transcribe(*args,**kwargs):
        return {'text':'测试','language':'zh','segments':[{'text':'测试','start':0,'end':1}],
                'asr_provenance':provenance}
    monkeypatch.setattr(whisper_service,'transcribe',transcribe)
    monkeypatch.setattr('backend.core.tool_runners._release_localization_torch_cache_sync',lambda:None)
    @asynccontextmanager
    async def gate(): yield
    profile=LocalizationProfile(id='qa',name='qa',asr_provider='faster_whisper',asr_model='small',
                                alignment_mode='off',diarization_mode='off')
    out=await svc.transcribe_for_localization(tmp_path/'source.wav',profile=profile,gpu_gate=gate)
    actual=out.metadata['asr']
    assert actual['device']==device
    assert actual['compute_type']==compute
    assert actual['executor_fallback_reason']==fallback
