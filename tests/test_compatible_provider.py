import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.core.ai_provider import AIProvider, AIClient, get_ai_client, PROVIDER_DEFAULTS
from backend.core.exceptions import AIKeyMissingError
from backend.config import settings


class TestCompatibleProvider(unittest.TestCase):

    def test_compatible_provider_config(self):
        """Verify compatible provider initializes with base_url, api_key, and model."""
        with patch.object(settings, "AI_PROVIDER", "compatible"), \
             patch.object(settings, "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"), \
             patch.object(settings, "AI_API_KEY", "test_key_123"), \
             patch.object(settings, "AI_MODEL", "gemini-2.5-flash"):
            
            client = get_ai_client()
            self.assertEqual(client.provider, AIProvider.COMPATIBLE)
            self.assertEqual(client.base_url, "https://generativelanguage.googleapis.com/v1beta/openai/")
            self.assertEqual(client.api_key, "test_key_123")
            self.assertEqual(client.model, "gemini-2.5-flash")

    def test_base_url_passed_correctly(self):
        """Verify base_url is properly passed when creating AsyncOpenAI client."""
        client = AIClient(
            provider=AIProvider.COMPATIBLE,
            api_key="test_key_xyz",
            model="gemini-2.5-flash",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        self.assertEqual(client.base_url, "https://generativelanguage.googleapis.com/v1beta/openai/")

    def test_keyless_compatible_endpoint_uses_local_placeholder(self):
        """Canonical compatible endpoints support keyless local servers."""
        with patch.object(settings, "AI_PROVIDER", "compatible"), \
             patch.object(settings, "AI_BASE_URL", "http://127.0.0.1:1234/v1"), \
             patch.object(settings, "AI_MODEL", "local-model"), \
             patch.object(settings, "AI_API_KEY", ""), \
             patch.object(settings, "GEMINI_API_KEY", ""), \
             patch.dict(os.environ, {"AI_API_KEY": "", "GEMINI_API_KEY": ""}, clear=False):
            client = get_ai_client()
            self.assertEqual(client.provider, AIProvider.COMPATIBLE)
            self.assertEqual(client.api_key, "local")
            self.assertEqual(client.model, "local-model")

    def test_missing_base_url_fails_clearly(self):
        """Verify failure when AI_BASE_URL is missing for compatible provider."""
        with patch.object(settings, "AI_PROVIDER", "compatible"), \
             patch.object(settings, "AI_BASE_URL", ""), \
             patch.object(settings, "AI_API_KEY", "test_key"), \
             patch.dict(os.environ, {"AI_BASE_URL": "", "AI_API_KEY": "test_key"}, clear=False):
            
            with self.assertRaises(ValueError) as ctx:
                get_ai_client()
            self.assertIn("AI_BASE_URL is required", str(ctx.exception))

    def test_existing_providers_no_regression(self):
        """Verify OpenAI, Anthropic, and OpenRouter behavior remains intact."""
        # 1. Anthropic fallback
        with patch.object(settings, "AI_PROVIDER", ""), \
             patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test"), \
             patch.object(settings, "OPENAI_API_KEY", ""), \
             patch.object(settings, "OPENROUTER_API_KEY", ""):
            client = get_ai_client()
            self.assertEqual(client.provider, AIProvider.ANTHROPIC)
            self.assertEqual(client.api_key, "sk-ant-test")

        # 2. OpenAI fallback
        with patch.object(settings, "AI_PROVIDER", ""), \
             patch.object(settings, "ANTHROPIC_API_KEY", ""), \
             patch.object(settings, "OPENAI_API_KEY", "sk-openai-test"), \
             patch.object(settings, "OPENROUTER_API_KEY", ""):
            client = get_ai_client()
            self.assertEqual(client.provider, AIProvider.OPENAI)
            self.assertEqual(client.api_key, "sk-openai-test")

        # 3. OpenRouter fallback
        with patch.object(settings, "AI_PROVIDER", ""), \
             patch.object(settings, "ANTHROPIC_API_KEY", ""), \
             patch.object(settings, "OPENAI_API_KEY", ""), \
             patch.object(settings, "OPENROUTER_API_KEY", "sk-or-test"):
            client = get_ai_client()
            self.assertEqual(client.provider, AIProvider.OPENROUTER)
            self.assertEqual(client.api_key, "sk-or-test")

    def test_vietnamese_language_in_studio(self):
        """The mounted Studio, not retired Translate, offers Vietnamese."""
        studio = APP_ROOT / "frontend/src/pages/StudioLocalize.jsx"
        content = studio.read_text(encoding="utf-8")
        self.assertIn('<MenuItem value="vi">Tiếng Việt</MenuItem>', content)
        self.assertIn('form.append("target_language", target)', content)

    def test_vietnamese_tts_voices_from_canonical_owner(self):
        """Studio consumes provider-owned voices rather than a hardcoded list."""
        from backend.services.edge_tts_service import RECOMMENDED_VOICES
        self.assertIn("vi-VN-HoaiMyNeural", RECOMMENDED_VOICES)
        self.assertIn("vi-VN-NamMinhNeural", RECOMMENDED_VOICES)
        speaker_panel = APP_ROOT / "frontend/src/components/tools/SpeakerVoicePanel.jsx"
        content = speaker_panel.read_text(encoding="utf-8")
        self.assertIn("freeSolo options={state.available_voices || []}", content)


if __name__ == "__main__":
    unittest.main()
