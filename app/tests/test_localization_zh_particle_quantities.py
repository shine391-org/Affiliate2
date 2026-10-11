"""Chinese softening particles that carry NO quantity (一下) — paired controls.

Seg-0001 of a real clip rejected a correct translation: '我的衣服可以脱一下' ->
'Áo tôi có thể cởi ra 1 chút'. 一點 is already a lexical degree; 一下 is the
same class of non-numeral (verb-reducing particle) and was still counted as 1.

These certify the quantity comparison only. They do not certify the translation
or the source ASR.
"""
import pytest

from backend.services.dubbing_rewrite_guard import (
    _numeric_text, check_translation_meaning)


def kinds(source, target):
    return {i['kind'] for i in check_translation_meaning(
        source, target, 'zh', 'Vietnamese', glossary={}, include_advisories=True)}


@pytest.mark.parametrize('source,target',[
    ('我的衣服可以脱一下','Áo tôi có thể cởi ra 1 chút'),
    ('我看一下书','Tôi xem sách'),
    ('等一下','Đợi một chút'),
    ('我们聊一下','Chúng ta nói chuyện một chút'),
    ('你听一下','Bạn nghe một chút'),
])
def test_yixia_particle_is_not_a_count(source, target):
    assert 'quantity_lost' not in kinds(source, target)


@pytest.mark.parametrize('source',['我看一下书', '等一下', '我的衣服可以脱一下'])
def test_yixia_disappears_from_the_normalized_comparison(source):
    assert '1' not in _numeric_text(source)


@pytest.mark.parametrize('source,mutated_target',[
    ('我有三个','Tôi có mười cái'),
    ('他走了两步','Anh ấy đi bảy bước'),
    ('买了两瓶水','Mua một chai nước'),
    ('看了十分钟','Xem ba mươi phút'),
])
def test_genuine_counts_still_blocked_when_changed(source, mutated_target):
    assert 'quantity_lost' in kinds(source, mutated_target)


def test_genuine_counts_still_survive_normalization():
    assert _numeric_text('我有三个') != _numeric_text('我有四个')
    assert '3' in _numeric_text('我有三个')


def test_yixia_does_not_hide_a_real_count_in_the_same_cue():
    assert kinds('我一下买了三个', 'Tôi mua ba cái') == set()
    assert 'quantity_lost' in kinds('我一下买了三个', 'Tôi mua một cái')