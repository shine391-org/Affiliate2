"""Punctuation-free location questions from real C6, plus loss/statement controls.

These certify speech-act invariants only, not the full translation or source ASR.
"""
import pytest
from backend.services.dubbing_rewrite_guard import check_translation_meaning


def kinds(source,target):
    return {i['kind'] for i in check_translation_meaning(source,target,'zh','Vietnamese',glossary={},include_advisories=True)}


@pytest.mark.parametrize('source,target',[
    ('你从哪里来啊','Bạn từ đâu đến vậy'),
    ('你最喜欢哪里','Bạn thích nơi nào nhất'),
    ('你去哪儿','Bạn đi đâu'),
    ('你住哪里','Bạn sống ở đâu'),
    ('你喜欢哪里','Bạn thích chỗ nào'),
    ('你住哪里','Cho tôi biết bạn sống ở đâu'),
    ('你知道我住在哪里吗','Bạn có biết tôi sống ở đâu không'),
])
def test_unpunctuated_location_question_is_not_lost(source,target):
    assert 'question_changed' not in kinds(source,target)


@pytest.mark.parametrize('source,target',[
    ('你从哪里来啊','Bạn từ chỗ đó đến'),
    ('你最喜欢哪里','Bạn thích nơi đó nhất'),
    ('你去哪儿','Bạn đi chỗ đó'),
    ('你住哪里','Bạn sống ở đây'),
])
def test_removing_the_location_question_remains_blocked(source,target):
    assert 'question_changed' in kinds(source,target)


@pytest.mark.parametrize('source,target',[
    ('我没有去任何地方','Tôi không đi đâu'),
    ('去任何地方都可以','Đi bất kỳ nơi nào cũng được'),
    ('我没有去任何地方','Tôi chưa đến đâu'),
])
def test_negative_anywhere_and_unrestricted_locations_are_not_questions(source,target):
    assert 'question_changed' not in kinds(source,target)


def test_added_location_question_to_statement_remains_blocked():
    assert 'question_changed' in kinds('我从那里来','Tôi từ đâu đến vậy')


@pytest.mark.parametrize('target',[
    'Tôi biết bạn sống ở đâu',
    'Tôi nhớ bạn sống ở đâu',
])
def test_embedded_where_in_assertion_cannot_replace_a_question(target):
    assert 'question_changed' in kinds('你住哪里',target)

