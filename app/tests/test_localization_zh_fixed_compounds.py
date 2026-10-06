"""五官 is a fixed compound ('facial features'), not a count of five things.

Real clip seg-0027: 你五官很好看 -> 'Ngũ quan của bạn rất đẹp' was rejected with
quantity_lost because 五 normalized to the numeral 5 while the correct
Vietnamese rendering has no numeral at all.

These certify the quantity comparison only, not the translation or the source
ASR.
"""
import pytest

from backend.services.dubbing_rewrite_guard import (
    _numeric_text, check_translation_meaning)


def kinds(source, target):
    return {i['kind'] for i in check_translation_meaning(
        source, target, 'zh', 'Vietnamese', glossary={}, include_advisories=True)}


@pytest.mark.parametrize('source,target',[
    ('你五官很好看','Ngũ quan của bạn rất đẹp'),
    ('他的五官很立体','Ngũ quan của anh ấy rất nổi bật'),
    ('五官精致','Ngũ quan tinh tế'),
    ('看看她的五官','Nhìn ngũ quan của cô ấy'),
])
def test_fixed_wuguan_compound_is_not_a_count(source, target):
    assert 'quantity_lost' not in kinds(source, target)


@pytest.mark.parametrize('source',['你五官很好看', '五官精致', '他的五官很立体'])
def test_wuguan_normalizes_without_a_numeral(source):
    assert '5' not in _numeric_text(source)


@pytest.mark.parametrize('source,mutated_target',[
    ('你五官很好看','Ngũ quan của bạn rất đẹp, thêm 5 người nữa'),
    ('五官精致','5 người rất tinh tế'),
])
def test_a_real_added_or_changed_count_is_still_blocked(source, mutated_target):
    # 'five features' is licensed, an explicit count elsewhere is not.
    assert 'quantity_lost' in kinds(source, mutated_target)


def test_five_of_something_real_is_still_a_count():
    assert _numeric_text('我买了五个') != _numeric_text('我买了四个')
    assert '5' in _numeric_text('我买了五个')
    assert 'quantity_lost' in kinds('我买了五个', 'Tôi mua bốn cái')