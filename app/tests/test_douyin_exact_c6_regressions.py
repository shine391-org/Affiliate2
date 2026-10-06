"""Real-reply regression for the two exact Douyin QA runs (guard v9).

Pairs are the authentic DeepSeek replies captured while job
3dc64d54-39f5-4f8d-b0b0-b029c354e158 (7690901218818440955) and
ba878da3-eb23-41db-ae62-dc22cab24368 (7692334515935196032) failed strict QC.
"""
import json
import pathlib

import pytest

from backend.services.dubbing_rewrite_guard import check_translation_meaning

RECORDED = json.loads((pathlib.Path(__file__).parent / "fixtures" / "douyin_exact_c6_replies.json").read_text(encoding="utf-8"))

# Each case is one segment the shipped guard rejected although the recorded
# invariant survived: the model kept the quantity/negation/unit/question.
PRESERVED = [
    ("7690901218818440955", "seg-0028", "quantity_lost", "看到你让我想起来一首歌", "Nhìn thấy bạn khiến tôi nhớ đến một bài hát"),
    ("7690901218818440955", "seg-0035", "quantity_lost", "但前面又有个女生很漂亮", "nhưng phía trước lại có một cô gái rất xinh"),
    ("7690901218818440955", "seg-0045", "question_changed", "喜欢吗 喜欢啊", "Thích không? Thích ạ"),
    ("7690901218818440955", "seg-0066", "negation_lost", "你这手机看得不错呀 我新买的", "Điện thoại của bạn trông được đấy, tôi mới mua"),
    ("7692334515935196032", "seg-0005", "quantity_lost", "如果超過四指", "nếu vượt quá 4 ngón"),
    ("7692334515935196032", "seg-0008", "unit_lost", "你現在就自拍", "Bây giờ bạn hãy tự chụp ảnh"),
    ("7692334515935196032", "seg-0010", "negation_lost", "能不能再塞下一隻眼睛", "có thể đặt thêm một con mắt nữa không"),
    ("7692334515935196032", "seg-0011", "quantity_lost", "如果剛好塞下一隻眼睛就眼睛剛好", "nếu vừa đặt vừa một con mắt thì mắt vừa đẹp"),
    ("7692334515935196032", "seg-0012", "quantity_lost", "如果塞下一隻眼睛", "nếu đặt được một con mắt"),
    ("7692334515935196032", "seg-0026", "unit_lost", "你的兩個瞳孔的內側", "phía trong 2 đồng tử của bạn không"),
    ("7692334515935196032", "seg-0027", "question_changed", "壞條垂直線有沒有超過這個距離", "đường thẳng đứng có vượt quá khoảng cách này không"),
    ("7692334515935196032", "seg-0055", "quantity_lost", "如果超過了一根中指的話", "Nếu vượt quá 1 ngón giữa"),
    ("7692334515935196032", "seg-0056", "negation_lost", "你那個脖子非常的先長", "thì cổ bạn rất thon dài"),
    ("7692334515935196032", "seg-0057", "unit_lost", "愛穿啥穿啥啥啥 啥都好看 第九 最後 側臉 九十度", "Thích mặc gì thì mặc, mặc gì cũng đẹp, thứ chín, cuối cùng, mặt nghiêng 90 độ"),
]


@pytest.mark.parametrize("source_id,segment_id,false_kind,source,target", PRESERVED)
def test_preserved_invariant_is_not_rejected(source_id, segment_id, false_kind, source, target):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert false_kind not in {issue["kind"] for issue in issues}, (source_id, segment_id, issues)


@pytest.mark.parametrize(
    "source,target,expected_kind",
    [
        ("如果超過4指", "Nếu vượt quá 5 ngón tay", "quantity_lost"),
        ("距離是5公里", "Khoảng cách là 5 mét", "unit_lost"),
        ("我不喜欢这首歌", "Tôi thích bài hát này", "negation_lost"),
        ("你喜欢这首歌吗", "Bạn thích bài hát này.", "question_changed"),
        ("买3个苹果", "Mua 2 quả táo", "quantity_lost"),
        ("身高是175厘米", "Chiều cao là 170 cm", "quantity_lost"),
    ],
)
def test_genuine_change_is_still_rejected(source, target, expected_kind):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert expected_kind in {issue["kind"] for issue in issues}, issues


def test_ordinary_lookalike_words_are_not_units():
    # 'bao giờ' (never), 'bao nhiêu tuổi' (how old) and 'đồng tử' (pupil)
    # must not be read as hour / age / currency units.
    assert check_translation_meaning(
        "有点紧张 没有被拍过", "Hơi căng thẳng, chưa từng bị chụp bao giờ", "zh", "vi", glossary={}
    ) == []
    assert check_translation_meaning(
        "怎么会小呀 你多大", "Sao lại nhỏ thế, bạn bao nhiêu tuổi", "zh", "vi", glossary={}
    ) == []


def test_recorded_pairs_still_reject_when_a_real_invariant_breaks():
    recorded = {(row["source_id"], row["segment_id"]): row for row in RECORDED}
    row = recorded[("7690901218818440955", "seg-0031")]
    broken = row["target"].replace("Tôi không biết", "Tôi biết")
    kinds = {issue["kind"] for issue in check_translation_meaning(row["source"], broken, "zh", "vi", glossary={})}
    assert "negation_lost" in kinds


def test_model_added_question_is_still_rejected():
    # The reply appends a question the source cue does not contain, so the
    # speech act really did change and the guard must keep rejecting it.
    kinds = {
        issue["kind"]
        for issue in check_translation_meaning(
            "你的兩個瞳孔的內側", "phía trong 2 đồng tử của bạn không", "zh", "vi", glossary={}
        )
    }
    assert "question_changed" in kinds


@pytest.mark.parametrize(
    "source,target,expected_kind,severity",
    [
        ("你現在就自拍", "Bây giờ bạn hãy tự chụp 1 tấm ảnh", "quantity_lost", "hard"),
        ("你是個月亮下巴 都下巴", "Bạn là 1 cái cằm mặt trăng, cằm dài", None, None),
        ("其實是最正的一個和面", "thì thật ra là 1 khuôn mặt hài hòa nhất", "article_asymmetry", "uncertain"),
    ],
)
def test_recorded_count_is_hard_but_ambiguous_article_requires_review(source, target, expected_kind, severity):
    # The disputed ASR/added clauses remain unadjudicated. Fixing cardinal vs
    # article classification must NOT certify these full translations as good.
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={}, include_advisories=True)
    if expected_kind is None:
        # Both sides express the indefinite article, not a changed cardinal.
        # This says nothing about the unadjudicated ASR/added-clause meaning.
        assert not {'quantity_lost','article_asymmetry'} & {i['kind'] for i in issues}
        return
    matching = [issue for issue in issues if issue["kind"] == expected_kind]
    assert matching, (source, target, issues)
    assert all(issue["severity"] == severity for issue in matching)
    assert all(issue["action"] == ("reject" if severity == "hard" else "review") for issue in matching)


@pytest.mark.parametrize(
    "source,target",
    [
        ("持续3个月", "Kéo dài 3 tháng"),
        ("买了5个苹果", "Mua 5 quả táo"),
        ("距离是3公里", "Khoảng cách là 3 km"),
        ("等了两个星期", "Đợi 2 tuần"),
    ],
)
def test_real_counts_and_measurements_still_pass(source, target):
    assert check_translation_meaning(source, target, "zh", "vi", glossary={}) == []