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

    assert 'label="Hướng dẫn dịch"' in ui
    assert 'label="Hướng dẫn viết lại C7"' in ui
    assert 'label="Quy tắc ngữ nghĩa C6"' in ui
    assert 'label="Quy tắc ngữ nghĩa C7"' in ui

    assert 'draft.translation_guardrails || ""' in ui
    assert 'draft.rewrite_guardrails || ""' in ui
    assert 'patch("translation_guardrails"' in ui
    assert 'patch("rewrite_guardrails"' in ui

    assert "Đặt lại mặc định C6" in ui
    assert "Đặt lại mặc định C7" in ui
    assert "Quy tắc bảo vệ prompt (Nâng cao)" in ui
    assert "ID/thứ tự ổn định và giao thức đầu ra JSON vẫn bị khóa" in ui
