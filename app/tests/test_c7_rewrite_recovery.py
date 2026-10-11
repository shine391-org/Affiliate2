"""Measured C7 rewrites: refusal is valid, transient != global unavailability."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment
from backend.core.ai_provider import AIProviderCooldownError
from backend.services import localization_service as svc
from backend.services.dubbing_planner import DubbingConfig,DubbingCue,build_plan

async def adapt(monkeypatch, response=None, error=None, deadline=None):
    original='Vậy tôi chụp em nhé'
    segments={
        'prev':LocalizationSegment(id='prev',source_text='你好',translated_text='Xin chào',source_start_ms=0,source_end_ms=1000,speaker_id='A'),
        's':LocalizationSegment(id='s',source_text='那我拍拍你',translated_text=original,source_start_ms=1000,source_end_ms=1860,speaker_id='B'),
        'next':LocalizationSegment(id='next',source_text='好',translated_text='Được',source_start_ms=1860,source_end_ms=2200,speaker_id='A')}
    plan,chunks=build_plan([DubbingCue(1,'s',1000,1860,original,timing_locked=True)],DubbingConfig())
    calls=[]
    async def leaf(*args,**kwargs):
        calls.append(kwargs)
        if error:raise error
        return response or json.dumps([{'id':'s','text':original}],ensure_ascii=False)
    monkeypatch.setattr(svc,'_call_ai_chat_resilient',leaf)
    state={'measured_duration_ms':1531,'attempt':1};issues=[]
    changed=await svc._adapt_chunk_items(object(),plan=plan,chunk=chunks[0],segment_by_id=segments,target_language='Vietnamese',target_ms=860,profile=LocalizationProfile(id='p',name='P'),state=state,issues=issues,deadline_at=deadline)
    return changed,state,issues,calls,segments

@pytest.mark.asyncio
async def test_safe_unchanged_reply_is_not_malformed_or_retried_as_shape_failure(monkeypatch):
    changed,state,issues,calls,segments=await adapt(monkeypatch)
    assert not changed and len(calls)==1
    assert state.get('no_safe_shortening') is True
    assert [i['reason'] for i in issues]==['rewrite_no_safe_shortening']
    assert segments['s'].translated_text=='Vậy tôi chụp em nhé'

@pytest.mark.asyncio
async def test_prompt_has_context_and_advisory_not_mandatory_word_quota(monkeypatch):
    _,_,_,calls,_=await adapt(monkeypatch)
    prompt=calls[0]['messages'][0]['content']
    assert 'advisory word budget' in prompt.lower()
    assert 'at most' not in prompt.lower()
    assert 'unchanged' in prompt.lower()
    assert 'Xin chào' in prompt and 'Được' in prompt and 'speaker_id' in prompt

@pytest.mark.asyncio
async def test_transient_transport_failure_does_not_disable_later_chunks(monkeypatch):
    _,state,issues,_,_=await adapt(monkeypatch,error=ConnectionError('temporary upstream reset'))
    assert not state.get('unavailable',False)
    assert state['retry_scope']=='chunk'
    assert issues[0]['reason']=='rewrite_ai_transient'

@pytest.mark.asyncio
async def test_admission_no_dispatch_is_distinct_from_provider_unavailable(monkeypatch):
    _,state,issues,_,_=await adapt(monkeypatch,error=AIProviderCooldownError('Provider cooldown/minimum gap cannot fit AI deadline; no dispatch'),deadline=asyncio.get_running_loop().time()+20)
    assert not state.get('unavailable',False)
    assert state['retry_scope']=='admission'
    assert issues[0]['reason']=='rewrite_ai_wait_budget'
    assert issues[0]['dispatched'] is False

@pytest.mark.asyncio
async def test_expired_shared_deadline_is_a_stage_budget_stop(monkeypatch):
    _,state,issues,_,_=await adapt(monkeypatch,error=TimeoutError('rewrite AI deadline exceeded'),deadline=asyncio.get_running_loop().time()-1)
    assert not state.get('unavailable',False)
    assert state['retry_scope']=='stage_budget'
    assert issues[0]['reason']=='rewrite_budget_exhausted'


@pytest.mark.asyncio
async def test_route_audit_counts_wire_dispatch_not_entry_to_cooldown_admission():
    from backend.core.ai_provider import AIClient
    class Client(AIClient):
        def __init__(self):
            self.model='test';self.provider=SimpleNamespace(value='compatible')
            self.connection_name='test';self.traffic_policy={};self.max_retries=0
        async def chat(self,**kwargs):
            raise AIProviderCooldownError('Provider cooldown/minimum gap cannot fit AI deadline; no dispatch')
    client=Client();route=svc._AIStageRouteState([client])
    with pytest.raises(AIProviderCooldownError):
        await svc._call_ai_chat_resilient(client,None,[{'role':'user','content':'test'}],route_state=route,max_attempts=1,total_timeout_s=1,stage='rewrite')
    assert route.attempt_count==0
    assert route.failure_chain[0]['dispatched'] is False
