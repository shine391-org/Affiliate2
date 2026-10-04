# SPDX-License-Identifier: AGPL-3.0-only
"""C6 regressions from real QA responses, with corruption controls for C6/C7."""
import json

import pytest

from backend.core.localization import LocalizationSegment, fast_localization_profile
from backend.services import localization_service as service
from backend.services.dubbing_rewrite_guard import check_rewrite_meaning, check_translation_meaning


@pytest.mark.parametrize("source,target", [
    ("我是一个节拍博主", "Em là một blogger nhịp điệu"),
    ("其中每一个概念的确切含义吗", "ý nghĩa chính xác của từng khái niệm trong số đó không"),
    ("每一个概念", "Mỗi khái niệm"),
    ("十八岁", "mười tám tuổi"),
    ("二十一岁", "hai mươi mốt tuổi"),
    ("两个相机", "hai máy ảnh"),
    ("五公斤", "năm kg"),
    ("一年", "một năm"),
    ("五年", "năm năm"),
    ("三十五分钟", "ba mươi lăm phút"),
    ("一百零五公斤", "một trăm linh năm kg"),
    ("二十万元", "hai trăm nghìn nhân dân tệ"),
    ("你明白吗", "Bạn hiểu không?"),
    ("你明白吗?", "Bạn có hiểu không?"),
    ("你不明白吗", "Bạn không hiểu không?"),
    ("这不是免费的吗", "Có phải nó không miễn phí không?"),
    ("你明白吗。然后再试", "Bạn hiểu không? Sau đó thử lại"),
    ("四台电脑", "bốn máy tính"),
    ("一台电脑", "một máy tính"),
    ("两台电视", "hai cái tivi"),
    ("三瓶水", "ba chai nước"),
    ("五本书", "năm quyển sách"),
    ("三个苹果", "ba quả táo"),
    ("八点钟", "Tám giờ"),
    ("你能说吗", "Bạn có thể nói không?"),
    ("不是一个博主", "Không phải một blogger"),
    ("你明白吗", "Bạn hiểu không ạ?"),
    ("一个一个地把这些概念拆开", "lần lượt phân tách từng khái niệm một"),
    ("我会邀请一些女孩子在海边拍照", "Tôi sẽ mời một số bạn nữ chụp ảnh bên biển."),
    ("你刷过节拍段子吗 嗯", "Bạn đã xem các đoạn nhịp điệu không? Ừ."),
])
def test_c6_accepts_equivalent_quantities_and_questions(source, target):
    assert not check_translation_meaning(source, target, "zh", "vi", glossary={})


@pytest.mark.parametrize("target", [
    "tách từng cái một những khái niệm này ra",
    "Tách từng cái một, những khái niệm này ra.",
    "Những khái niệm này cần được tách từng cái một",
    "Tách từng khái niệm một.",
    "Tách lần lượt từng cái một.",
    "Phân tách mỗi khái niệm một.",
])
def test_c6_one_by_one_is_not_a_cardinal_article(target):
    # Captured DeepSeek reply, plus grammar/position variants; comparison only.
    assert not check_translation_meaning(
        "一个一个地把这些概念拆开", target, "zh", "vi", glossary={})


@pytest.mark.parametrize("source,target,kind", [
    ("一个一个地拆开两个相机", "Tháo từng cái một ba máy ảnh", "quantity_lost"),
    ("一个一个地称五公斤", "Cân từng cái một năm mét", "unit_lost"),
    ("一个一个地问十八岁的人", "Hỏi từng người một mười chín tuổi", "quantity_lost"),
    ("不要一个一个地拆开", "Tháo từng cái một", "negation_lost"),
    ("一个一个地拆开吗", "Tháo từng cái một", "question_changed"),
])
def test_c6_distributive_normalization_preserves_other_invariants(source, target, kind):
    assert kind in {x["kind"] for x in check_translation_meaning(
        source, target, "zh", "vi", glossary={})}


def test_c6_distributive_still_protects_names():
    issues = check_translation_meaning("一个一个地问张三", "Hỏi từng người một", "zh", "vi",
                                       glossary={"张三": "Trương Tam"})
    assert "term_lost" in {x["kind"] for x in issues}


@pytest.mark.parametrize("source,target,kind", [
    ("两个相机", "ba máy ảnh", "quantity_lost"),
    ("十八岁", "mười chín tuổi", "quantity_lost"),
    ("十八岁", "mười tám", "unit_lost"),
    ("五公斤", "năm mét", "unit_lost"),
    ("用五公斤和十米", "Dùng năm mét và mười kg", "unit_lost"),
    ("每一个概念", "khái niệm", "quantity_lost"),
    ("你不明白吗", "Bạn hiểu không?", "negation_lost"),
    ("你明白吗", "Bạn không hiểu không?", "negation_added"),
    ("你明白吗", "Bạn hiểu rồi", "question_changed"),
    ("这不是免费的", "Miễn phí", "negation_lost"),
    ("这是免费的", "Không miễn phí", "negation_added"),
    ("你明白吗?", "Không. Bạn hiểu không?", "negation_added"),
    ("你明白吗?", "Bạn hiểu không? Không.", "negation_added"),
    ("你明白吗", "Bạn hiểu. Không.", "question_changed"),
    ("你明白吗", "Câu trả lời là không.", "question_changed"),
    ("三瓶水", "năm chai nước", "quantity_lost"),
    ("五本书", "ba quyển sách", "quantity_lost"),
    ("两个相机", "hai máy ảnh và một ống kính", "quantity_lost"),
    ("一个一个地把这些概念拆开", "Phân tách những khái niệm này", "quantity_lost"),
])
def test_c6_still_blocks_real_corruption(source, target, kind):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert kind in {issue["kind"] for issue in issues}


@pytest.mark.parametrize("source,target,glossary", [
    ("张三和吴晓彤", "Trương Tam và Ngô Tiểu Đồng", {"张三": "Trương Tam", "吴晓彤": "Ngô Tiểu Đồng"}),
    ("爸爸来了", "Ba đến rồi", {}),
    ("今年来了", "Năm nay đã đến", {}),
    ("五公斤", "năm kg", {}),
    ("这个苹果熟了", "Quả táo này chín rồi", {}),
    ("他很成熟", "Anh ấy chín chắn", {}),
    ("他们在聊天", "Họ đang tám chuyện", {}),
    ("我哥哥", "Anh Hai", {}),
    ("阿海来了", "Hai đến rồi", {"阿海": "Hai"}),
])
def test_c6_does_not_treat_names_or_ordinary_words_as_numbers(source, target, glossary):
    assert not check_translation_meaning(source, target, "zh", "vi", glossary=glossary)


@pytest.mark.parametrize('source,target', [
    ('十年', 'mười năm'), ('十五年', 'mười lăm năm'), ('五年', 'năm năm'),
    ('今年', 'Năm nay'), ('五公斤', 'năm kg'),
])
def test_review_year_homograph_uses_context_not_integer_parser_alone(source, target):
    assert not check_translation_meaning(source, target, 'zh', 'vi', glossary={})


@pytest.mark.parametrize('text', ['Bạn hiểu không?', 'Bạn không hiểu không?',
                                 'Bạn hiểu không ạ?', 'Câu trả lời là không.'])
def test_c6_vietnamese_identity_does_not_strip_only_one_side(text):
    assert not check_translation_meaning(text, text, 'vi', 'vi', glossary={})


@pytest.mark.parametrize('source,target,kind', [
    ('Bạn không hiểu không?', 'Bạn hiểu không?', 'negation_lost'),
    ('Bạn hiểu không?', 'Bạn không hiểu không?', 'negation_added'),
    ('Bạn hiểu không?', 'Bạn hiểu rồi.', 'question_changed'),
])
def test_c6_vietnamese_questions_preserve_real_negations(source, target, kind):
    assert kind in {issue['kind'] for issue in check_translation_meaning(
        source, target, 'vi', 'vi', glossary={})}


@pytest.mark.parametrize("before,after,kind", [
    ("Chị xem video này không", "Chị xem video này", "negation_lost"),
    ("Có phải nó không miễn phí không?", "Có phải nó miễn phí không?", "negation_lost"),
    ("Bạn hiểu không?", "Bạn hiểu rồi.", "question_changed"),
    ("Có hai máy ảnh", "Có ba máy ảnh", "quantity_lost"),
    ("Nặng năm kg", "Nặng năm mét", "unit_lost"),
])
def test_c7_still_blocks_loss_from_approved_text(before, after, kind):
    issues = check_rewrite_meaning(before, after, "vi")
    assert kind in {issue["kind"] for issue in issues}


@pytest.mark.asyncio
async def test_real_qa_responses_pass_canonical_c6_without_retry():
    pairs = [
        ("我是一个节拍博主", "Em là một blogger nhịp điệu"),
        ("其中每一个概念的确切含义吗", "ý nghĩa chính xác của từng khái niệm trong số đó không"),
    ]
    batch = [LocalizationSegment(id=f"s{index}", source_text=source,
        source_start_ms=index * 2000, source_end_ms=(index + 1) * 2000)
        for index, (source, _) in enumerate(pairs)]

    class RecordedResponse:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return json.dumps([{"id": segment.id, "text": target}
                for segment, (_, target) in zip(batch, pairs)], ensure_ascii=False)

    ai = RecordedResponse()
    texts, failed = await service._translate_batch_resilient(ai, batch,
        source_language="zh", target_language="Vietnamese", context=" ".join(source for source, _ in pairs),
        profile=fast_localization_profile(), for_dub=True)
    assert texts == [target for _, target in pairs]
    assert failed == set()
    assert ai.calls == 1
