"""Static UI contract for profile-scoped prompt controls."""
from pathlib import Path


def test_localization_profiles_ui_exposes_editable_guardrails_and_locked_protocol_notice():
    ui = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "components"
        / "settings"
        / "LocalizationProfilesSection.jsx"
    ).read_text(encoding="utf-8")

    assert 'label="Translation instructions"' in ui
    assert 'label="C7 rewrite instructions"' in ui
    assert 'label="C6 semantic guardrails"' in ui
    assert 'label="C7 semantic guardrails"' in ui

    assert 'draft.translation_guardrails || ""' in ui
    assert 'draft.rewrite_guardrails || ""' in ui
    assert 'patch("translation_guardrails"' in ui
    assert 'patch("rewrite_guardrails"' in ui

    assert "Reset C6 default" in ui
    assert "Reset C7 default" in ui
    assert "Prompt Guardrails (Advanced)" in ui
    assert "stable IDs/order and JSON output protocol stay locked" in ui
