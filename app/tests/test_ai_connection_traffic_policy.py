# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Task 4 — Settings API + frontend optional traffic_policy.

Covers:
- normalize_ai_traffic_policy() validation and canonical defaults
- make_ai_client() traffic_policy passthrough
- get_ai_client() active ai_connections_json resolution
- Settings upsert PATCH-like semantics (omit preserves, null clears)
- GET /settings sanitization includes traffic_policy
- /settings/ai/test resolves saved policy + optional unsaved override
- Archived compatibility unchanged
- Two aliases sharing one resource_group
- Invalid values rejected (NaN/Inf/negative/unknown fields/non-bool enabled)
- C6/C7 executor binding via _ai_client_for_connection
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.core import ai_provider as ap
from backend.core.ai_provider import (
    AIClient,
    AIProvider,
    get_ai_client,
    make_ai_client,
    normalize_ai_traffic_policy,
)


# ── normalize_ai_traffic_policy ─────────────────────────────────────────────


class TestNormalizeTrafficPolicy:
    def test_none_returns_none(self):
        assert normalize_ai_traffic_policy(None) is None

    def test_empty_dict_returns_canonical_defaults(self):
        result = normalize_ai_traffic_policy({})
        assert result == {
            "enabled": False,
            "min_gap_s": 10.0,
            "cooldown_initial_s": 30.0,
            "cooldown_max_s": 300.0,
            "resource_group": None,
        }

    def test_partial_fields_get_defaults(self):
        result = normalize_ai_traffic_policy({"enabled": True})
        assert result["enabled"] is True
        assert result["min_gap_s"] == 10.0
        assert result["cooldown_initial_s"] == 30.0
        assert result["cooldown_max_s"] == 300.0
        assert result["resource_group"] is None

    def test_full_fields_preserved(self):
        result = normalize_ai_traffic_policy({
            "enabled": True,
            "min_gap_s": 5.0,
            "cooldown_initial_s": 15.0,
            "cooldown_max_s": 120.0,
            "resource_group": "hermes-cluster",
        })
        assert result == {
            "enabled": True,
            "min_gap_s": 5.0,
            "cooldown_initial_s": 15.0,
            "cooldown_max_s": 120.0,
            "resource_group": "hermes-cluster",
        }

    def test_resource_group_empty_string_becomes_none(self):
        result = normalize_ai_traffic_policy({"resource_group": ""})
        assert result["resource_group"] is None

    def test_resource_group_whitespace_becomes_none(self):
        result = normalize_ai_traffic_policy({"resource_group": "   "})
        assert result["resource_group"] is None

    def test_unknown_field_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown traffic_policy field"):
            normalize_ai_traffic_policy({"bogus": 1})

    def test_nan_min_gap_raises(self):
        with pytest.raises(ValueError, match="nan|inf|negative|finite"):
            normalize_ai_traffic_policy({"min_gap_s": float("nan")})

    def test_inf_cooldown_raises(self):
        with pytest.raises(ValueError, match="nan|inf|negative|finite"):
            normalize_ai_traffic_policy({"cooldown_max_s": float("inf")})

    def test_negative_gap_raises(self):
        with pytest.raises(ValueError, match="negative"):
            normalize_ai_traffic_policy({"min_gap_s": -1})

    def test_negative_cooldown_initial_raises(self):
        with pytest.raises(ValueError, match="negative"):
            normalize_ai_traffic_policy({"cooldown_initial_s": -0.1})

    def test_negative_cooldown_max_raises(self):
        with pytest.raises(ValueError, match="negative"):
            normalize_ai_traffic_policy({"cooldown_max_s": -1})

    def test_non_bool_enabled_raises(self):
        with pytest.raises(ValueError, match="enabled.*bool"):
            normalize_ai_traffic_policy({"enabled": "yes"})

    def test_int_enabled_raises(self):
        with pytest.raises(ValueError, match="enabled.*bool"):
            normalize_ai_traffic_policy({"enabled": 1})

    def test_non_numeric_min_gap_raises(self):
        with pytest.raises(ValueError, match="min_gap_s"):
            normalize_ai_traffic_policy({"min_gap_s": "fast"})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="dict"):
            normalize_ai_traffic_policy("not a dict")

    def test_list_raises(self):
        with pytest.raises(ValueError, match="dict"):
            normalize_ai_traffic_policy([1, 2, 3])

    def test_zero_values_allowed(self):
        result = normalize_ai_traffic_policy({
            "min_gap_s": 0,
            "cooldown_initial_s": 0,
            "cooldown_max_s": 0,
        })
        assert result["min_gap_s"] == 0.0
        assert result["cooldown_initial_s"] == 0.0
        assert result["cooldown_max_s"] == 0.0

    def test_canonical_output_is_new_dict(self):
        original = {"enabled": True}
        result = normalize_ai_traffic_policy(original)
        assert result is not original
        assert original == {"enabled": True}  # input not mutated


# ── make_ai_client traffic_policy passthrough ────────────────────────────────


class TestMakeAIClientTrafficPolicy:
    def test_traffic_policy_none_by_default(self):
        client = make_ai_client("openai", api_key="sk-x", model="gpt-4")
        assert client.traffic_policy is None

    def test_traffic_policy_stored_on_client(self):
        policy = normalize_ai_traffic_policy({
            "enabled": True,
            "min_gap_s": 5.0,
            "cooldown_initial_s": 15.0,
            "cooldown_max_s": 120.0,
            "resource_group": "rg1",
        })
        client = make_ai_client(
            "openai",
            api_key="sk-x",
            model="gpt-4",
            traffic_policy=policy,
        )
        assert client.traffic_policy == policy

    def test_traffic_policy_none_explicit(self):
        client = make_ai_client(
            "openai",
            api_key="sk-x",
            model="gpt-4",
            traffic_policy=None,
        )
        assert client.traffic_policy is None

    def test_invalid_traffic_policy_raises(self):
        with pytest.raises(ValueError):
            make_ai_client(
                "openai",
                api_key="sk-x",
                model="gpt-4",
                traffic_policy={"enabled": "not-a-bool"},
            )


# ── get_ai_client active connection resolution ──────────────────────────────


class TestGetAIClientTrafficPolicy:
    def _settings_with_connections(self, connections: dict, active_id: str):
        return SimpleNamespace(
            ai_provider="compatible",
            ai_model="test-model",
            ai_base_url="http://localhost:9999/v1",
            ai_api_key_encrypted=None,
            ai_connection_id=active_id,
            ai_connections_json=json.dumps(connections),
        )

    def test_no_connections_no_policy(self):
        s = SimpleNamespace(
            ai_provider="openai",
            ai_api_key_encrypted=None,
            ai_model=None,
            ai_base_url=None,
            ai_connection_id=None,
            ai_connections_json=None,
        )
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            with pytest.raises(Exception):
                get_ai_client(s)

    def test_active_connection_policy_reaches_client(self):
        connections = {
            "hermes-a": {
                "label": "Hermes A",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 10.0,
                    "cooldown_initial_s": 30.0,
                    "cooldown_max_s": 300.0,
                    "resource_group": "hermes-cluster",
                },
            },
        }
        s = self._settings_with_connections(connections, "hermes-a")
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = get_ai_client(s)
        assert client.traffic_policy is not None
        assert client.traffic_policy["enabled"] is True
        assert client.traffic_policy["resource_group"] == "hermes-cluster"

    def test_inactive_connection_policy_not_used(self):
        connections = {
            "hermes-a": {
                "label": "Hermes A",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 5.0,
                    "cooldown_initial_s": 15.0,
                    "cooldown_max_s": 120.0,
                    "resource_group": "rg-a",
                },
            },
            "hermes-b": {
                "label": "Hermes B",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8646/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 20.0,
                    "cooldown_initial_s": 60.0,
                    "cooldown_max_s": 600.0,
                    "resource_group": "rg-b",
                },
            },
        }
        s = self._settings_with_connections(connections, "hermes-a")
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = get_ai_client(s)
        assert client.traffic_policy["resource_group"] == "rg-a"

    def test_connection_without_policy_gets_none(self):
        connections = {
            "plain": {
                "label": "Plain",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
            },
        }
        s = self._settings_with_connections(connections, "plain")
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = get_ai_client(s)
        assert client.traffic_policy is None

    def test_archived_policy_not_borrowed_by_legacy_active_scalar_fallback(self):
        connections = {
            "archived-conn": {
                "label": "Old",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": True,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 5.0,
                    "cooldown_initial_s": 15.0,
                    "cooldown_max_s": 120.0,
                    "resource_group": "old-rg",
                },
            },
        }
        s = self._settings_with_connections(connections, "archived-conn")
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = get_ai_client(s)
        assert client.base_url == s.ai_base_url
        assert client.model == s.ai_model
        assert client.traffic_policy is None


# ── Settings API upsert semantics ───────────────────────────────────────────


class TestSettingsUpsertTrafficPolicy:
    """Test the PATCH-like semantics for traffic_policy on AI connections."""

    def _make_settings_row(self, connections=None):
        from backend.models.user_settings import UserSettings
        row = UserSettings(user_id="local")
        row.ai_connections_json = json.dumps(connections or {})
        row.ai_provider = "compatible"
        row.ai_model = "test-model"
        row.ai_base_url = "http://localhost:9999/v1"
        row.ai_api_key_encrypted = None
        row.ai_connection_id = None
        row.ai_provider_profiles_json = None
        return row

    def _fake_session(self, row):
        class Result:
            def scalar_one_or_none(self):
                return row

        class FakeSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False
            async def execute(self, _query):
                return Result()
            def add(self, _row):
                return None
            async def commit(self):
                return None

        return FakeSession()

    @pytest.mark.asyncio
    async def test_new_connection_with_policy(self):
        """Creating a new connection with traffic_policy stores it."""
        from backend.api import settings as settings_api

        row = self._make_settings_row()
        fake_session = self._fake_session(row)

        with patch.object(settings_api, "AsyncSessionLocal", lambda: fake_session):
            result = await settings_api.upsert_ai_connection(
                settings_api.AIConnectionRequest(
                    label="Test",
                    provider="compatible",
                    model="longcat",
                    base_url="http://127.0.0.1:8645/v1",
                    traffic_policy={
                        "enabled": True,
                        "min_gap_s": 10.0,
                        "cooldown_initial_s": 30.0,
                        "cooldown_max_s": 300.0,
                        "resource_group": "test-rg",
                    },
                )
            )

        assert "test" in result.ai_connections
        tp = result.ai_connections["test"]["traffic_policy"]
        assert tp is not None
        assert tp["enabled"] is True
        assert tp["resource_group"] == "test-rg"

    @pytest.mark.asyncio
    async def test_omitted_traffic_policy_preserves_existing(self):
        """PATCH-like: omitting traffic_policy on edit preserves the saved value."""
        from backend.api import settings as settings_api

        existing_policy = {
            "enabled": True,
            "min_gap_s": 5.0,
            "cooldown_initial_s": 15.0,
            "cooldown_max_s": 120.0,
            "resource_group": "preserve-me",
        }
        row = self._make_settings_row({
            "edit-conn": {
                "label": "Edit",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": existing_policy,
            },
        })
        row.ai_connection_id = "edit-conn"
        fake_session = self._fake_session(row)

        with patch.object(settings_api, "AsyncSessionLocal", lambda: fake_session):
            result = await settings_api.upsert_ai_connection(
                settings_api.AIConnectionRequest(
                    connection_id="edit-conn",
                    label="Edit",
                    provider="compatible",
                    model="longcat",
                    base_url="http://127.0.0.1:8645/v1",
                    # traffic_policy NOT supplied — should be preserved
                )
            )

        tp = result.ai_connections["edit-conn"]["traffic_policy"]
        assert tp == existing_policy

    @pytest.mark.asyncio
    async def test_explicit_null_traffic_policy_clears(self):
        """Explicit null traffic_policy clears the saved value."""
        from backend.api import settings as settings_api

        row = self._make_settings_row({
            "clear-conn": {
                "label": "Clear",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 5.0,
                    "cooldown_initial_s": 15.0,
                    "cooldown_max_s": 120.0,
                    "resource_group": "clear-me",
                },
            },
        })
        row.ai_connection_id = "clear-conn"
        fake_session = self._fake_session(row)

        with patch.object(settings_api, "AsyncSessionLocal", lambda: fake_session):
            result = await settings_api.upsert_ai_connection(
                settings_api.AIConnectionRequest(
                    connection_id="clear-conn",
                    label="Clear",
                    provider="compatible",
                    model="longcat",
                    base_url="http://127.0.0.1:8645/v1",
                    traffic_policy=None,  # explicit null clears
                )
            )

        tp = result.ai_connections["clear-conn"]["traffic_policy"]
        assert tp is None

    @pytest.mark.asyncio
    async def test_connection_without_policy_stays_without(self):
        """Connections that never had traffic_policy remain compatible."""
        from backend.api import settings as settings_api

        row = self._make_settings_row({
            "legacy": {
                "label": "Legacy",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
            },
        })
        row.ai_connection_id = "legacy"
        fake_session = self._fake_session(row)

        with patch.object(settings_api, "AsyncSessionLocal", lambda: fake_session):
            result = await settings_api.upsert_ai_connection(
                settings_api.AIConnectionRequest(
                    connection_id="legacy",
                    label="Legacy",
                    provider="compatible",
                    model="longcat",
                    base_url="http://127.0.0.1:8645/v1",
                )
            )

        tp = result.ai_connections["legacy"].get("traffic_policy")
        assert tp is None

    @pytest.mark.asyncio
    async def test_invalid_traffic_policy_rejected(self):
        """Invalid traffic_policy values are rejected with 400."""
        from backend.api import settings as settings_api

        row = self._make_settings_row()
        fake_session = self._fake_session(row)

        with patch.object(settings_api, "AsyncSessionLocal", lambda: fake_session):
            with pytest.raises(Exception) as exc_info:
                await settings_api.upsert_ai_connection(
                    settings_api.AIConnectionRequest(
                        label="Bad",
                        provider="compatible",
                        model="longcat",
                        base_url="http://127.0.0.1:8645/v1",
                        traffic_policy={"enabled": "not-a-bool"},
                    )
                )
            assert exc_info.value.status_code == 400


# ── GET /settings sanitization ──────────────────────────────────────────────


class TestSettingsGetSanitization:
    def test_public_connections_include_traffic_policy(self):
        from backend.api.settings import _public_ai_connections

        row = SimpleNamespace(
            ai_connections_json=json.dumps({
                "test-conn": {
                    "label": "Test",
                    "provider": "compatible",
                    "model": "longcat",
                    "base_url": "http://127.0.0.1:8645/v1",
                    "api_key_encrypted": None,
                    "archived": False,
                    "traffic_policy": {
                        "enabled": True,
                        "min_gap_s": 10.0,
                        "cooldown_initial_s": 30.0,
                        "cooldown_max_s": 300.0,
                        "resource_group": "rg1",
                    },
                },
            }),
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="test-conn",
        )
        result = _public_ai_connections(row)
        assert "traffic_policy" in result["test-conn"]
        assert result["test-conn"]["traffic_policy"]["enabled"] is True

    def test_public_connections_policy_none_when_absent(self):
        from backend.api.settings import _public_ai_connections

        row = SimpleNamespace(
            ai_connections_json=json.dumps({
                "plain": {
                    "label": "Plain",
                    "provider": "compatible",
                    "model": "longcat",
                    "base_url": "http://127.0.0.1:8645/v1",
                    "api_key_encrypted": None,
                    "archived": False,
                },
            }),
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="plain",
        )
        result = _public_ai_connections(row)
        assert result["plain"].get("traffic_policy") is None

    def test_inactive_archived_connections_excluded(self):
        from backend.api.settings import _public_ai_connections

        row = SimpleNamespace(
            ai_connections_json=json.dumps({
                "archived": {
                    "label": "Old",
                    "provider": "compatible",
                    "model": "longcat",
                    "base_url": "http://127.0.0.1:8645/v1",
                    "api_key_encrypted": None,
                    "archived": True,
                    "traffic_policy": {
                        "enabled": True,
                        "min_gap_s": 5.0,
                        "cooldown_initial_s": 15.0,
                        "cooldown_max_s": 120.0,
                        "resource_group": "old-rg",
                    },
                },
            }),
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="current",
        )
        result = _public_ai_connections(row)
        assert "archived" not in result


# ── Probe /test resolves saved policy ───────────────────────────────────────


class TestProbeResolvesSavedPolicy:
    def test_probe_uses_saved_connection_policy(self):
        """/settings/ai/test must resolve the selected saved policy."""
        from backend.api.settings import _ai_probe_connection

        row = SimpleNamespace(
            ai_connections_json=json.dumps({
                "test-conn": {
                    "label": "Test",
                    "provider": "compatible",
                    "model": "longcat",
                    "base_url": "http://127.0.0.1:8645/v1",
                    "api_key_encrypted": None,
                    "archived": False,
                    "traffic_policy": {
                        "enabled": True,
                        "min_gap_s": 10.0,
                        "cooldown_initial_s": 30.0,
                        "cooldown_max_s": 300.0,
                        "resource_group": "probe-rg",
                    },
                },
            }),
        )
        body = SimpleNamespace(
            connection_id="test-conn",
            provider=None,
            model=None,
            base_url=None,
            api_key=None,
        )
        cfg = _ai_probe_connection(row, body)
        assert cfg["traffic_policy"]["enabled"] is True
        assert cfg["traffic_policy"]["resource_group"] == "probe-rg"

    def test_probe_without_connection_id_no_policy(self):
        from backend.api.settings import _ai_probe_connection

        row = SimpleNamespace(ai_connections_json=None)
        body = SimpleNamespace(
            connection_id=None,
            provider="openai",
            model=None,
            base_url=None,
            api_key="sk-test",
        )
        result = _ai_probe_connection(row, body)
        assert result == {}


# ── Two aliases sharing one resource_group ─────────────────────────────────


class TestAliasesShareResourceGroup:
    def test_two_aliases_same_resource_group(self):
        """Two connections with the same resource_group share pacing."""
        connections = {
            "hermes-a": {
                "label": "Hermes A",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 10.0,
                    "cooldown_initial_s": 30.0,
                    "cooldown_max_s": 300.0,
                    "resource_group": "shared-cluster",
                },
            },
            "hermes-b": {
                "label": "Hermes B",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8646/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 10.0,
                    "cooldown_initial_s": 30.0,
                    "cooldown_max_s": 300.0,
                    "resource_group": "shared-cluster",
                },
            },
        }
        row = SimpleNamespace(
            ai_connections_json=json.dumps(connections),
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="hermes-a",
        )
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = get_ai_client(row)
        assert client.traffic_policy["resource_group"] == "shared-cluster"

    def test_different_resource_groups_independent(self):
        """Different resource_groups are independent."""
        connections = {
            "conn-a": {
                "label": "A",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 5.0,
                    "cooldown_initial_s": 15.0,
                    "cooldown_max_s": 120.0,
                    "resource_group": "rg-a",
                },
            },
            "conn-b": {
                "label": "B",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8646/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 20.0,
                    "cooldown_initial_s": 60.0,
                    "cooldown_max_s": 600.0,
                    "resource_group": "rg-b",
                },
            },
        }
        row = SimpleNamespace(
            ai_connections_json=json.dumps(connections),
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="conn-a",
        )
        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = get_ai_client(row)
        assert client.traffic_policy["resource_group"] == "rg-a"
        assert client.traffic_policy["min_gap_s"] == 5.0


# ── Archived compatibility ──────────────────────────────────────────────────


class TestArchivedCompatibility:
    def test_old_connection_format_loads(self):
        """Connections saved before traffic_policy existed still load."""
        from backend.api.settings import _load_ai_connections

        old_format = {
            "old-conn": {
                "label": "Old",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
            },
        }
        row = SimpleNamespace(
            ai_connections_json=json.dumps(old_format),
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="old-conn",
        )
        loaded = _load_ai_connections(row)
        assert "old-conn" in loaded
        assert loaded["old-conn"]["provider"] == "compatible"

    def test_legacy_provider_snapshot_still_works(self):
        """Legacy ai_provider_profiles_json fallback still loads."""
        from backend.api.settings import _load_ai_connections

        row = SimpleNamespace(
            ai_connections_json=None,
            ai_provider_profiles_json=json.dumps({
                "openai": {
                    "model": "gpt-4",
                    "base_url": None,
                    "api_key_encrypted": None,
                },
            }),
            ai_provider="openai",
            ai_model="gpt-4",
            ai_base_url=None,
            ai_api_key_encrypted=None,
            ai_connection_id=None,
        )
        loaded = _load_ai_connections(row)
        assert "openai" in loaded
        assert loaded["openai"]["provider"] == "openai"


# ── C6/C7 executor binding ──────────────────────────────────────────────────


class TestC6C7ExecutorBinding:
    """Verify that traffic_policy reaches the executor for C6 and C7 routes."""

    def _make_settings_row(self, connections, active_id):
        from backend.models.user_settings import UserSettings
        row = UserSettings(user_id="local")
        row.ai_connections_json = json.dumps(connections)
        row.ai_provider = "compatible"
        row.ai_model = "longcat"
        row.ai_base_url = "http://127.0.0.1:8645/v1"
        row.ai_api_key_encrypted = None
        row.ai_connection_id = active_id
        return row

    def test_c6_route_resolves_traffic_policy(self):
        """C6 translation route must resolve traffic_policy from saved connection."""
        from backend.services.localization_service import _ai_client_for_connection

        connections = {
            "hermes-c6": {
                "label": "Hermes C6",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 10.0,
                    "cooldown_initial_s": 30.0,
                    "cooldown_max_s": 300.0,
                    "resource_group": "c6-cluster",
                },
            },
        }
        row = self._make_settings_row(connections, "hermes-c6")

        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = _ai_client_for_connection(row, "hermes-c6")

        assert client.traffic_policy is not None
        assert client.traffic_policy["enabled"] is True
        assert client.traffic_policy["resource_group"] == "c6-cluster"

    def test_c7_route_resolves_traffic_policy(self):
        """C7 rewrite route must resolve traffic_policy from saved connection."""
        from backend.services.localization_service import _ai_client_for_connection

        connections = {
            "hermes-c7": {
                "label": "Hermes C7",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8646/v1",
                "api_key_encrypted": None,
                "archived": False,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 15.0,
                    "cooldown_initial_s": 45.0,
                    "cooldown_max_s": 200.0,
                    "resource_group": "c7-cluster",
                },
            },
        }
        row = self._make_settings_row(connections, "hermes-c7")

        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = _ai_client_for_connection(row, "hermes-c7")

        assert client.traffic_policy is not None
        assert client.traffic_policy["enabled"] is True
        assert client.traffic_policy["resource_group"] == "c7-cluster"
        assert client.traffic_policy["min_gap_s"] == 15.0

    def test_c6_route_without_policy_gets_none(self):
        """C6 route without traffic_policy gets None (backward compat)."""
        from backend.services.localization_service import _ai_client_for_connection

        connections = {
            "plain-c6": {
                "label": "Plain C6",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": False,
            },
        }
        row = self._make_settings_row(connections, "plain-c6")

        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = _ai_client_for_connection(row, "plain-c6")

        assert client.traffic_policy is None

    def test_c6_route_archived_connection_no_policy(self):
        """C6 route with archived connection gets None policy."""
        from backend.services.localization_service import _ai_client_for_connection

        connections = {
            "archived-c6": {
                "label": "Archived C6",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": True,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 5.0,
                    "cooldown_initial_s": 15.0,
                    "cooldown_max_s": 120.0,
                    "resource_group": "old-rg",
                },
            },
        }
        row = self._make_settings_row(connections, "archived-c6")

        with patch.object(ap, "settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENROUTER_API_KEY = ""
            mock_settings.AI_PROVIDER = ""
            mock_settings.AI_BASE_URL = ""
            mock_settings.AI_API_KEY = ""
            mock_settings.AI_MODEL = ""
            mock_settings.GEMINI_API_KEY = ""
            client = _ai_client_for_connection(row, "archived-c6")

        # Archived connection should not resolve policy
        assert client.traffic_policy is None

class TestArchivedConnectionPolicyIsFailClosed:
    """An archived row is RESERVED FOR EXISTING JOBS, so it must still resolve
    its endpoint and its pacing policy. Silently dropping the policy here
    would hand a saved job an unpaced route to the SAME upstream resource —
    exactly the amplification this feature removes. The API already refuses
    to archive the ACTIVE connection (HTTP 409), so 'active AND archived' is
    only reachable through legacy rows; when it happens the safe direction is
    to keep pacing, not to drop it.
    """

    def test_archived_active_scalar_still_resolves_its_policy(self):
        from backend.services.localization_service import _saved_ai_connection_config

        connections = {
            "archived-active": {
                "label": "Old",
                "provider": "compatible",
                "model": "longcat",
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_encrypted": None,
                "archived": True,
                "traffic_policy": {
                    "enabled": True,
                    "min_gap_s": 5.0,
                    "cooldown_initial_s": 15.0,
                    "cooldown_max_s": 120.0,
                    "resource_group": "old-rg",
                },
            },
        }
        row = SimpleNamespace(
            ai_provider="compatible",
            ai_model="longcat",
            ai_base_url="http://127.0.0.1:8645/v1",
            ai_api_key_encrypted=None,
            ai_connection_id="archived-active",
            ai_connections_json=json.dumps(connections),
            ai_provider_profiles_json=None,
        )
        saved = _saved_ai_connection_config(row, "archived-active")
        assert saved["base_url"] == "http://127.0.0.1:8645/v1"
        assert saved["traffic_policy"]["enabled"] is True
        assert saved["traffic_policy"]["resource_group"] == "old-rg"
