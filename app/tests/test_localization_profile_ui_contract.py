"""Static UI contract for profile-scoped translation/rewrite instructions."""
from pathlib import Path


def test_localization_profiles_ui_exposes_locked_core_and_c7_custom_instructions():
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
    assert 'draft.rewrite_instructions || ""' in ui
    assert 'patch("rewrite_instructions"' in ui
    assert "Core translation guardrails are locked by the app." in ui
    assert "Core rewrite guardrails are locked by the app." in ui
