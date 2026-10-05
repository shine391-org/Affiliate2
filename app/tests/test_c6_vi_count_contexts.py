"""Independent-review regression: Vietnamese count contexts and degree words.

Independent review (deleg_361bdd8b) found the zh→vi C6 guard rejects faithful
counts because the Vietnamese count-classifier set is incomplete. Proven at
HEAD before this file existed: 三件事 -> 'ba việc' returned quantity_lost with
source {3} and target {}. Two distinct classes, both reproduced from real
runs and the review packet.
"""
import pytest

from backend.services.dubbing_rewrite_guard import check_translation_meaning

# A count whose Vietnamese classifier noun was missing from the parser's
# count-context set, so the target side read as no count at all.
COUNT_NOW_RECOGNISED = [
    ("三件事", "ba việc"),
    ("三个例子", "ba ví dụ"),
    ("三个方法", "ba cách"),
    ("三个问题", "ba câu hỏi"),
    ("三篇文章", "ba bài viết"),
]


@pytest.mark.parametrize("source,target", COUNT_NOW_RECOGNISED)
def test_faithful_count_is_not_rejected(source, target):
    assert check_translation_meaning(source, target, "zh", "vi", glossary={}) == []


@pytest.mark.parametrize(
    "source,target",
    [
        ("適合出一些承擔的course", "phù hợp với 1 số kiểu tóc có độ phồng"),
        ("因為要適合的寶寶都會有一點", "Vì những bạn phù hợp đều sẽ có 1 chút"),
    ],
)
def test_degree_quantifier_is_not_a_cardinal(source, target):
    """一些/一點 carry no cardinal, and Vietnamese renders them as '1 số'/'1 chút'."""
    assert check_translation_meaning(source, target, "zh", "vi", glossary={}) == []


@pytest.mark.parametrize(
    "source,target,expected_kind",
    [
        ("三件事", "hai việc", "quantity_lost"),
        ("三个例子", "ba cách và 1 ví dụ", "quantity_lost"),
        ("持续3个月", "Kéo dài 1 số tháng", "quantity_lost"),
        ("三张照片", "hai tấm ảnh", "quantity_lost"),
    ],
)
def test_genuine_count_change_is_still_rejected(source, target, expected_kind):
    kinds = {issue["kind"] for issue in check_translation_meaning(source, target, "zh", "vi", glossary={})}
    assert expected_kind in kinds, (source, target, kinds)


@pytest.mark.parametrize(
    "source,target",
    [
        ("持续3个月", "Kéo dài 3 tháng"),
        ("三张照片", "3 tấm ảnh"),
        ("有三个人", "có 3 người"),
        ("每个视频有三个步骤", "mỗi video có ba bước"),
    ],
)
def test_real_counts_still_pass(source, target):
    assert check_translation_meaning(source, target, "zh", "vi", glossary={}) == []