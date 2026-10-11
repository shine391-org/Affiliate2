"""C6 production caller integration; no provider/meaning judge claim."""
import json
import pytest
from backend.core.localization import LocalizationProfile, LocalizationSegment
from backend.services import localization_service as svc


def seg(id, source, start, end, speaker='A'):
    return LocalizationSegment(id=id, source_text=source, source_start_ms=start,
                               source_end_ms=end, speaker_id=speaker)


def candidate(rows, targets, **kwargs):
    raw=json.dumps([{'id':s.id,'text':t} for s,t in zip(rows,targets)],ensure_ascii=False)
    return svc._translation_candidates(raw,rows,{},LocalizationProfile(id='qa',name='QA'),'zh','Vietnamese',**kwargs)


def test_c6_caller_accepts_complete_question_across_adjacent_cues():
    rows=[seg('a','看你的鼻翼有沒有超過',30000,32380),seg('b','你的兩個瞳孔的內側',32380,34600)]
    accepted,rejected,_=candidate(rows,['Xem cánh mũi của bạn có vượt quá','phía trong 2 đồng tử của bạn không'])
    assert set(accepted)=={'a','b'}
    assert rejected=={}


@pytest.mark.parametrize('targets',[
    ['Xem cánh mũi của bạn vượt quá','phía trong 2 đồng tử của bạn'],
    ['Xem cánh mũi của bạn có vượt quá','phía trong 3 đồng tử của bạn không'],
])
def test_c6_caller_blocks_group_question_and_quantity_mutations(targets):
    rows=[seg('a','看你的鼻翼有沒有超過',30000,32380),seg('b','你的兩個瞳孔的內側',32380,34600)]
    _,rejected,_=candidate(rows,targets)
    assert rejected


def test_c6_caller_does_not_use_distant_question_to_license_age():
    rows=[seg('q','你多大',0,500),seg('a','我23了',30000,31000)]
    accepted,rejected,_=candidate(rows,['Bạn bao nhiêu tuổi?','Tôi 23 tuổi rồi'])
    assert 'a' in rejected


def test_c6_caller_receives_previous_age_context_in_targeted_repair():
    question=seg('q','你多大',0,600,speaker='B')
    answer=seg('a','我23了',650,1300)
    accepted,rejected,_=candidate([answer],['Tôi 23 tuổi rồi'],context_segments=[question,answer])
    assert set(accepted)=={'a'} and not rejected
    _,rejected,_=candidate([answer],['Tôi 24 tuổi rồi'],context_segments=[question,answer])
    assert 'a' in rejected


def test_neighbour_independent_question_cannot_hide_lost_question():
    rows=[seg('a','这个价格有没有超过',0,1800),seg('b','五百太贵了对不对',1800,3600)]
    accepted,rejected,_=candidate(rows,['Giá này vượt quá','500 quá đắt, phải không?'])
    assert 'a' not in accepted and 'a' in rejected
    assert 'question_changed' in {i['kind'] for i in rejected['a']}


@pytest.mark.asyncio
async def test_complete_c6_entrypoint_keeps_group_through_final_revalidation(monkeypatch,tmp_path):
    from backend.core.localization import LocalizationTranscript
    rows=[seg('a','看你的鼻翼有沒有超過',30000,32380),seg('b','你的兩個瞳孔的內側',32380,34600)]
    class AI:
        async def chat(self, **kwargs):
            return json.dumps([{'id':'a','text':'Xem cánh mũi của bạn có vượt quá'},
                               {'id':'b','text':'phía trong 2 đồng tử của bạn không'}],ensure_ascii=False)
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_:[AI()])
    out=await svc.translate_transcript(LocalizationTranscript(language='zh',segments=rows),
        target_language='Vietnamese',profile=LocalizationProfile(id='qa',name='qa'),
        user_settings=None,for_dub=True,checkpoint_dir=tmp_path)
    assert all(s.translation_status=='success' for s in out.segments)


@pytest.mark.asyncio
async def test_uncertain_article_is_preserved_with_explicit_review_under_strict_qc(monkeypatch,tmp_path):
    from backend.core.localization import LocalizationTranscript
    row=seg('a','其實是最正的一個和面',0,1000)
    class AI:
        async def chat(self,**kwargs):
            return json.dumps([{'id':'a','text':'thì thật ra là 1 khuôn mặt hài hòa nhất'}],ensure_ascii=False)
    monkeypatch.setattr(svc,'_profile_ai_route_clients_compat',lambda *_:[AI()])
    out=await svc.translate_transcript(LocalizationTranscript(language='zh',segments=[row]),
        target_language='Vietnamese',profile=LocalizationProfile(id='qa',name='qa',qc_profile='strict'),
        user_settings=None,for_dub=True,checkpoint_dir=tmp_path)
    assert out.segments[0].translation_status=='success'
    issues=out.metadata['translation_guard_review']['a']
    assert any(i['kind']=='article_asymmetry' and i['action']=='review' for i in issues)
