"""Regression guards for retired cookie/discovery ownership."""
from __future__ import annotations

from backend.api import settings as settings_api
from backend.models.user_settings import UserSettings


def test_settings_response_exposes_only_canonical_browser_session_state():
    fields = settings_api.SettingsResponse.model_fields
    assert "download_cookie_browser" in fields
    assert "download_cookie_profile" in fields
    assert "douyin_cookie_set" not in fields
    assert "tiktok_cookie_set" not in fields


def test_user_settings_retire_douyin_cookie_columns_from_orm():
    columns = set(UserSettings.__table__.columns.keys())
    assert "douyin_cookie_encrypted" not in columns
    assert "douyin_cookie_set_at" not in columns
    # TikTok keeps one upload-only legacy cookie fallback for now.
    assert "tiktok_cookie_encrypted" in columns


def test_manual_cookie_test_endpoint_is_gone():
    paths = {getattr(route, "path", "") for route in settings_api.router.routes}
    assert "/settings/test-cookie" not in paths
    assert "/settings/download-session/test" in paths
