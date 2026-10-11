# SPDX-License-Identifier: AGPL-3.0-only
"""Six C6 parsing regressions; examples are synthetic variants, not live QA."""
import json
import re

import pytest

from backend.core.localization import LocalizationSegment, fast_localization_profile
from backend.services import localization_service as service
from backend.services.dubbing_rewrite_guard import (
    _negations, _numbers, check_rewrite_meaning, check_translation_meaning,
)


EQUIVALENT = [
    # Lexical negation differs from an internal idiom morpheme. Prohibitions
    # remain negative even when Vietnamese uses the lexical verb 'cấm'.
    pytest.param("不准进入", "Cấm vào", id="lexical-prohibition"),
    pytest.param("这里不准拍照", "Ở đây cấm chụp ảnh", id="lexical-prohibition-photo"),
    pytest.param("不准吸烟", "Cấm hút thuốc", id="lexical-prohibition-smoking"),
    pytest.param("测得不准", "Đo sai", id="lexical-inaccurate"),
    pytest.param("预测不准", "Dự đoán thiếu chính xác", id="lexical-inaccurate-prediction"),
    pytest.param("不准确", "Không chính xác", id="lexical-inaccurate-adjective"),
    pytest.param("禁止进入", "Cấm vào", id="lexical-prohibition-synonym"),
    pytest.param("广告无孔不入", "Quảng cáo len lỏi khắp nơi", id="idiom-pervasive"),
    pytest.param("他不知不觉睡着了", "Anh ấy vô thức ngủ thiếp đi", id="idiom-unaware"),
    pytest.param("他不知不觉睡着了", "Anh ấy ngủ thiếp đi lúc nào không hay", id="idiom-unaware-lexical-negative"),
    pytest.param("他们不约而同笑了", "Họ đồng loạt bật cười", id="idiom-unanimous"),
    pytest.param("一劳永逸", "Giải quyết dứt điểm một lần", id="numeric-idiom"),
    pytest.param("只要一点点", "Chỉ cần một chút", id="numeric-small-amount"),
    pytest.param("高一点", "Cao hơn một chút", id="numeric-comparative"),
    pytest.param("再高一点点", "Cao thêm một tí nữa", id="numeric-comparative-repeated"),
    pytest.param("一勞永逸", "Giải quyết triệt để", id="numeric-traditional"),
    pytest.param("高一点五米", "Cao 1.5 mét", id="numeric-real-decimal"),
    pytest.param("你知道吗", "Bạn biết chứ", id="ma-chu"),
    pytest.param("你明白吗", "Bạn hiểu hả", id="ma-ha"),
    pytest.param("可以吗", "Được chứ nhỉ", id="ma-tag"),
    pytest.param("你知道吗，继续看", "Bạn biết không, xem tiếp nhé", id="ma-comma"),
    pytest.param("你知道嗎", "Bạn biết chứ?", id="ma-traditional"),
    pytest.param("你知道吗", "Biết không", id="vi-short-tag"),
    pytest.param("你听见了吗", "Nghe thấy chưa", id="vi-chua"),
    pytest.param("你准备好了吗", "Sẵn sàng chưa ạ", id="vi-chua-polite"),
    pytest.param("三分之一", "một phần ba", id="fraction-third"),
    pytest.param("三分之二", "hai phần ba", id="fraction-two-thirds"),
    pytest.param("四分之三", "ba phần tư", id="fraction-three-quarters"),
    pytest.param("3分之1", "1/3", id="fraction-arabic"),
    pytest.param("百分之五十", "50%", id="fraction-percent"),
    pytest.param("百分之五十", "năm mươi phần trăm", id="fraction-percent-words"),
    pytest.param("三分之一公斤", "một phần ba kg", id="fraction-unit"),
    pytest.param("下一期", "Kỳ tiếp theo", id="next-episode"),
    pytest.param("下一个视频", "Video kế tiếp", id="next-classifier"),
    pytest.param("下一次", "Lần sau", id="next-occurrence"),
    pytest.param("下一章", "Chương tiếp theo", id="next-chapter"),
    pytest.param("第二个视频", "Video thứ hai", id="ordinal-second"),
    pytest.param("第三次", "Lần thứ ba", id="ordinal-third"),
    pytest.param("下一步", "Bước tiếp theo", id="next-step"),
    pytest.param("下次", "Lần sau", id="next-without-one"),
    pytest.param("接下来", "Tiếp theo", id="next-sequence"),
    pytest.param("然后再试", "Sau đó thử lại", id="next-then"),
    pytest.param("第二章第三节", "Chương thứ hai mục thứ ba", id="ordinal-binding"),
    pytest.param("第一个视频", "Video đầu tiên", id="ordinal-first"),
    pytest.param("第一次", "Lần thứ nhất", id="ordinal-first-nhat"),
    pytest.param("星期一", "Thứ hai", id="ordinal-weekday-homograph"),
    pytest.param("商品下架了", "Sản phẩm đã gỡ khỏi kệ", id="next-morpheme-take-down"),
    pytest.param("他下台了", "Anh ấy đã từ chức", id="next-morpheme-step-down"),
    pytest.param("然后再试", "Rồi thử lại", id="next-connective"),
    pytest.param("你明白了", "Bạn hiểu rồi", id="next-completed-aspect"),
    pytest.param("下一年", "Năm sau", id="next-year"),
]


@pytest.mark.parametrize("source,target", EQUIVALENT)
def test_c6_accepts_structural_equivalence(source, target):
    assert not check_translation_meaning(source, target, "zh", "vi", glossary={})


@pytest.mark.parametrize("source,target", [("Biết không", "Bạn biết chứ"),
    ("Nghe thấy chưa", "Bạn nghe thấy chưa?"), ("Sẵn sàng chưa ạ", "Sẵn sàng chưa?")])
def test_vi_c6_identifies_questions_without_source_punctuation(source, target):
    assert not check_translation_meaning(source, target, "vi", "vi", glossary={})


@pytest.mark.parametrize("source,target,kind", [
    ("不准进入", "Được vào", "negation_lost"),
    ("不准吸烟", "Hút thuốc", "negation_lost"),
    ("测得不准", "Đo chính xác", "negation_lost"),
    ("预测不准", "Dự đoán chuẩn", "negation_lost"),
    ("不准确也不要修改", "Sai nhưng hãy sửa", "negation_lost"),
    ("允许拍照", "Cấm chụp ảnh", "negation_added"),
    ("广告并不是无孔不入", "Quảng cáo len lỏi khắp nơi", "negation_lost"),
    ("不要不约而同地离开", "Đồng loạt rời đi", "negation_lost"),
    ("不准只用五公斤", "Cấm chỉ dùng sáu kg", "quantity_lost"),
    ("高一米", "Cao một chút", "quantity_lost"),
    ("高一点五米", "Cao 5 mét", "quantity_lost"),
    ("禁止进入", "Được vào", "negation_lost"),
    ("一点点水和两瓶油", "Một chút nước và ba chai dầu", "quantity_lost"),
    ("高一点但不要超过五米", "Cao hơn một chút nhưng quá năm mét", "negation_lost"),
    ("高一点到五米", "Cao hơn một chút đến sáu mét", "quantity_lost"),
    ("你知道吗", "Bạn biết rồi", "question_changed"),
    ("你知道了", "Bạn biết chứ", "question_changed"),
    ("你不知道吗", "Bạn biết chứ", "negation_lost"),
    ("你知道吗", "Bạn không biết chứ", "negation_added"),
    ("你知道吗", "Câu trả lời là không", "question_changed"),
    ("你知道吗", "Tôi nói không", "question_changed"),
    ("Biết không", "Biết rồi", "question_changed"),
    ("Bạn không biết chứ", "Bạn biết chứ", "negation_lost"),
    ("Nghe thấy chưa", "Chưa nghe thấy", "question_changed"),
    ("三分之一", "hai phần ba", "quantity_lost"),
    ("三分之二", "ba phần hai", "quantity_lost"),
    ("四分之三", "ba phần năm", "quantity_lost"),
    ("三分之一", "một và ba", "quantity_lost"),
    ("百分之五十", "60%", "quantity_lost"),
    ("下一期", "Kỳ này", "ordinal_changed"),
    ("下一个视频", "Video trước", "ordinal_changed"),
    ("下一次", "Lần đầu", "ordinal_changed"),
    ("第二个视频", "Video thứ ba", "ordinal_changed"),
    ("第三次", "Ba lần", "ordinal_changed"),
    ("第二章第三节", "Chương thứ ba mục thứ hai", "ordinal_changed"),
    ("一劳永逸只要五公斤", "Giải quyết dứt điểm một lần chỉ cần sáu kg", "quantity_lost"),
    ("无孔不入的广告不要看", "Xem quảng cáo len lỏi khắp nơi", "negation_lost"),
    ("下一期有两个例子", "Kỳ tiếp theo có ba ví dụ", "quantity_lost"),
    ("不要看下一个视频", "Xem video tiếp theo", "negation_lost"),
    ("下一章吗", "Chương tiếp theo", "question_changed"),
    ("下一年", "Năm nay", "ordinal_changed"),
])
def test_c6_rejects_structural_corruption(source, target, kind):
    language = "vi" if source in {"Biết không", "Bạn không biết chứ", "Nghe thấy chưa"} else "zh"
    assert kind in {issue["kind"] for issue in check_translation_meaning(
        source, target, language, "vi", glossary={})}


def test_predicates_do_not_confuse_internal_morphemes_or_fraction_order():
    assert _negations("不是无孔不入", "zh") == 1
    assert _negations("无孔不入", "zh") == 0
    assert _negations("不准进入", "zh") == _negations("Cấm vào", "vi") == 1
    assert _numbers("一劳永逸") == _numbers("Làm để giải quyết dứt điểm")
    assert _numbers("一点点") == _numbers("một chút")
    assert _numbers("三分之二") == _numbers("hai phần ba") == _numbers("2/3")


@pytest.mark.parametrize("before,after,kind", [
    ("Cấm vào", "Vào", "negation_lost"),
    ("Dùng hai phần ba", "Dùng một phần ba", "quantity_lost"),
    ("Video thứ hai", "Hai video", "ordinal_changed"),
    ("Video tiếp theo", "Video trước", "ordinal_changed"),
])
def test_c7_preserves_lexical_negation_fractions_and_ordinals(before, after, kind):
    assert kind in {issue["kind"] for issue in check_rewrite_meaning(before, after, "vi")}


@pytest.mark.asyncio
async def test_six_classes_use_canonical_c6_without_repair_or_text_changes():
    pairs = [(case.values[0], case.values[1]) for case in EQUIVALENT]
    batch = [LocalizationSegment(id=f"s{index}", source_text=source,
        source_start_ms=index * 2000, source_end_ms=(index + 1) * 2000)
        for index, (source, _) in enumerate(pairs)]

    class SyntheticResponse:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return json.dumps([{"id": segment.id, "text": target}
                for segment, (_, target) in zip(batch, pairs)
                if segment.id in re.findall(r'"id":\s*"([^" ]+)"', kwargs["messages"][0]["content"])], ensure_ascii=False)

    ai = SyntheticResponse()
    texts, failed = await service._translate_batch_resilient(ai, batch,
        source_language="zh", target_language="Vietnamese", context=" ".join(source for source, _ in pairs),
        profile=fast_localization_profile(), for_dub=True)
    assert texts == [target for _, target in pairs]
    assert failed == set()
    assert ai.calls == 1
