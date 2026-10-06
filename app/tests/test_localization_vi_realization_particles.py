"""Vietnamese realization particles inside a sentence are NOT interrogatives.

Real clip seg-0026: source '你同学 你好漂亮啊' (啊 = exclamation) was translated
'Bạn học của bạn à, bạn xinh quá'. The 'à' sits BEFORE a comma, i.e. it closes a
clause inside a running sentence ('it's your classmate, how pretty') — a
realization particle, not a question. A genuinely clause-final interrogative
particle still counts.

These certify the speech act only, not the translation or the source ASR.
"""
import pytest

from backend.services.dubbing_rewrite_guard import (
    _translation_question, check_translation_meaning)


def kinds(source, target):
    return {i['kind'] for i in check_translation_meaning(
        source, target, 'zh', 'Vietnamese', glossary={}, include_advisories=True)}


@pytest.mark.parametrize('target',[
    'Bạn học của bạn à, bạn xinh quá',
    'Học của bạn à, cô ấy xinh thật',
])
def test_realization_particle_inside_sentence_is_not_a_question(target):
    assert _translation_question(target, 'Vietnamese') is False


@pytest.mark.parametrize('target',[
    'Bạn học của bạn à?',
    'Bạn học của bạn à',
    'Bạn muốn đi không?',
    'Bạn muốn đi không',
    'Có phải bạn học của bạn hả?',
    'Sao vậy, bạn học của bạn à?',
])
def test_clause_final_interrogative_still_counts(target):
    assert _translation_question(target, 'Vietnamese') is True


def test_real_cue_mid_sentence_particle_is_not_flagged():
    assert 'question_changed' not in kinds('你同学 你好漂亮啊', 'Bạn học của bạn à, bạn xinh quá')


def test_clause_final_particle_on_this_cue_is_still_flagged():
    # The source has no question at all, so the same particle moved to the end
    # of the target IS a speech-act change and must stay blocked.
    assert 'question_changed' in kinds('你同学 你好漂亮啊', 'Bạn học của bạn à?')