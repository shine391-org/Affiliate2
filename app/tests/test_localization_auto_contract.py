"""Explicit auto opt-in, legacy fidelity and policy invalidation contracts."""
import json
import pytest
from pydantic import ValidationError
from backend.core.localization import (LocalizationProfile, LocalizationSegment,
    default_localization_profile, fast_localization_profile, load_profile_store)


def test_legacy_and_hq_remain_strict():
    raw = {'id': 'old', 'name': 'Old profile', 'qc_profile': 'strict'}
    profile = LocalizationProfile.model_validate(raw)
    assert getattr(profile, 'translation_automation', None) == 'strict'
    store = load_profile_store(json.dumps({'default_profile_id': 'old', 'profiles': [raw]}))
    assert getattr(store.profiles[0], 'translation_automation', None) == 'strict'
    assert getattr(default_localization_profile(), 'translation_automation', None) == 'strict'


def test_new_fast_preset_selects_auto_explicitly_without_loosening_qc():
    profile = fast_localization_profile()
    assert getattr(profile, 'translation_automation', None) == 'auto'
    assert profile.qc_profile == 'strict'
    assert profile.model_dump()['translation_automation'] == 'auto'


def test_auto_profile_round_trip_does_not_silently_discard_policy():
    profile = LocalizationProfile(id='a', name='Auto', translation_automation='auto')
    persisted = profile.model_dump_json()
    assert json.loads(persisted).get('translation_automation') == 'auto'
    assert LocalizationProfile.model_validate_json(persisted).translation_automation == 'auto'


@pytest.mark.parametrize('bad', ['pass_everything', '', True])
def test_unknown_policy_is_rejected_not_silently_coerced(bad):
    with pytest.raises(ValidationError):
        LocalizationProfile(id='a', name='Auto', translation_automation=bad)


def test_draft_round_trip_is_not_success():
    segment = LocalizationSegment(id='s', source_text='三公斤', translated_text='4 kg',
        source_start_ms=0, source_end_ms=2000, translation_status='draft', qc_status='review')
    restored = LocalizationSegment.model_validate_json(segment.model_dump_json())
    assert restored.translation_status == 'draft'
    assert restored.translation_status != 'success'
    assert restored.qc_status == 'review'


def test_policy_override_invalidates_translation_not_just_qc():
    from backend.api.localization import _profile_invalidation
    assert _profile_invalidation({'translation_automation'}) == 'translation'
