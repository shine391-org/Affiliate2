"""Regression: an ADDED item must not pass as an indefinite article.

Vietnamese writes the indefinite article 'một' the same way it writes the
cardinal 'one'. The guard distinguishes them by the word that follows:

    'một chiếc kính'   -> classifier, so it COUNTS ("one lens")
    'một người'        -> bare noun, so it is an ARTICLE ("a person")

That distinction only works while the classifier/unit list covers the noun. A
counted noun outside that list ('một ống kính') is read as an article, the '1'
is subtracted from the count, and a translation that ADDS an entire item -
"two cameras" becoming "two cameras and a lens" - leaves the comparison
balanced and passes the guard.

`detail`: severity 'hard' only for a difference that cannot be an article;
article-shaped differences stay 'uncertain'/'review' as before, because the
role really is ambiguous without a lexicon.
"""
from __future__ import annotations

import pytest

from backend.services.dubbing_rewrite_guard import check_translation_meaning


def _kinds(source: str, target: str) -> set[str]:
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={}) or []
    return {issue["kind"] for issue in issues}


# ── the hole ──────────────────────────────────────────────────────────────


def test_adding_a_counted_item_outside_the_classifier_list_is_caught():
    """'một ống kính' adds a lens; the guard must not read 'một' as an article."""
    kinds = _kinds("两个相机", "hai máy ảnh và một ống kính")
    assert "quantity_lost" in kinds, (
        "the added 'một ống kính' was read as the indefinite article, so the "
        f"count compared equal and the added item passed; got {kinds}"
    )


@pytest.mark.parametrize(
    "source,target",
    [
        ("两个相机", "hai máy ảnh và một chiếc kính"),
        ("两个相机", "hai máy ảnh và một máy ảnh"),
        ("三瓶水", "năm chai nước và một ly"),
        ("五本书", "ba quyển sách và một cuốn"),
    ],
)
def test_adding_a_counted_item_is_caught_for_every_shape(source, target):
    """The same hole, expressed with the nouns a translator actually writes."""
    kinds = _kinds(source, target)
    assert kinds & {"quantity_lost", "unit_lost"}, (
        f"an added item went unnoticed for {source!r} -> {target!r}: {kinds}"
    )


# ── the safe direction must not regress ──────────────────────────────────
#
# A fix that widens "counted" too far turns ordinary indefinite articles into
# counts, which would block valid translations. These are the negatives.


@pytest.mark.parametrize(
    "source,target,note",
    [
        ("两个相机", "hai máy ảnh", "same count, no article"),
        ("一个苹果", "một quả táo", "'quả' is a classifier: one apple"),
        ("张三", "Trương Tam", "a name that contains a numeral"),
        ("爸爸来了", "Ba đến rồi", "'ba' as pronoun, not the number three"),
        ("他很成熟", "Anh ấy chín chắn", "'chín' as 'very', not nine"),
        ("我的哥哥", "Anh Hai", "'Hai' as a name, not two"),
        ("这个问题", "Câu hỏi này", "no numeral at all"),
    ],
)
def test_valid_translations_are_still_accepted(source, target, note):
    kinds = _kinds(source, target)
    assert not kinds, f"{note}: a valid translation was blocked by {kinds}"