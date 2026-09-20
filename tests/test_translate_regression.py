import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

viralmint_path = os.path.abspath("tools/ViralMint")
if viralmint_path not in sys.path:
    sys.path.insert(0, viralmint_path)

from backend.core.tool_runners import _translation_prompt, _LANG_NAME_MAP


class TestTranslateRegression(unittest.IsolatedAsyncioTestCase):

    def test_translation_prompt_semantics_captions_only(self):
        """When is_dub=False (captions_only), preserve upstream semantics: do NOT shorten."""
        prompt = _translation_prompt("Vietnamese", ["Hello world"], is_dub=False)
        self.assertIn("Preserve meaning — do NOT shorten, expand, or reorder lines.", prompt)
        self.assertNotIn("concise enough to fit approximately", prompt)

    def test_translation_prompt_semantics_full_dub(self):
        """When is_dub=True (full_dub), apply dubbing conciseness instructions."""
        prompt = _translation_prompt("Vietnamese", ["Hello world"], is_dub=True)
        self.assertIn("Keep each translated segment concise enough to fit approximately within the source segment timing.", prompt)
        self.assertIn("You translate video captions for dubbing.", prompt)

    def test_lang_name_map(self):
        """Verify language code 'vi' maps to 'Vietnamese'."""
        prompt_vi = _translation_prompt("vi", ["Test line"], is_dub=False)
        self.assertIn("natural, conversational Vietnamese", prompt_vi)
        self.assertNotIn("conversational vi", prompt_vi)

    @patch("backend.services.ffmpeg_service.convert_aspect_ratio", new_callable=AsyncMock)
    @patch("backend.core.tool_runners._is_already_vertical", new_callable=AsyncMock)
    @patch("backend.services.whisper_service.whisper_service.transcribe", new_callable=AsyncMock)
    @patch("backend.core.tool_runners._translate_segments", new_callable=AsyncMock)
    @patch("backend.services.caption_service.generate_captions_ass", new_callable=AsyncMock)
    @patch("backend.services.caption_service.burn_captions", new_callable=AsyncMock)
    @patch("backend.services.video_utils.probe_duration", return_value=10.0)
    async def test_normal_translate_no_aspect_preserves_resolution(
        self, mock_probe, mock_burn, mock_ass, mock_trans, mock_whisper, mock_vert, mock_convert
    ):
        """When target_aspect is None, convert_aspect_ratio must NOT be called."""
        from backend.core.tool_runners import run_tool_translate
        from backend.models.job import Job
        from backend.agents.job_helper import create_job

        mock_whisper.return_value = {"segments": [{"start": 0.0, "end": 2.0, "text": "test"}]}
        mock_trans.return_value = [{"start": 0.0, "end": 2.0, "text": "dịch"}]
        mock_ass.return_value = Path("dummy.ass")
        mock_convert.return_value = Path("dummy_reframe.mp4")

        # Fake existing out file
        with patch("pathlib.Path.exists", return_value=True):
            await run_tool_translate(
                job_id="test_no_aspect",
                in_path=Path("dummy_in.mp4"),
                target_language="Vietnamese",
                mode="captions_only",
                voice_id="",
                caption_style="viral",
                emoji_style="moderate",
                target_aspect=None,  # default: preserve source
            )

        # convert_aspect_ratio must NOT have been called!
        mock_convert.assert_not_called()

    @patch("backend.services.ffmpeg_service.convert_aspect_ratio", new_callable=AsyncMock)
    @patch("backend.core.tool_runners._is_already_vertical", new_callable=AsyncMock)
    @patch("backend.services.whisper_service.whisper_service.transcribe", new_callable=AsyncMock)
    @patch("backend.core.tool_runners._translate_segments", new_callable=AsyncMock)
    @patch("backend.services.caption_service.generate_captions_ass", new_callable=AsyncMock)
    @patch("backend.services.caption_service.burn_captions", new_callable=AsyncMock)
    @patch("backend.services.video_utils.probe_duration", return_value=10.0)
    async def test_explicit_9x16_triggers_reframe(
        self, mock_probe, mock_burn, mock_ass, mock_trans, mock_whisper, mock_vert, mock_convert
    ):
        """When target_aspect='9:16' and video is horizontal, convert_aspect_ratio MUST be called."""
        from backend.core.tool_runners import run_tool_translate

        mock_whisper.return_value = {"segments": [{"start": 0.0, "end": 2.0, "text": "test"}]}
        mock_trans.return_value = [{"start": 0.0, "end": 2.0, "text": "dịch"}]
        mock_ass.return_value = Path("dummy.ass")
        mock_convert.return_value = Path("dummy_reframe.mp4")
        mock_vert.return_value = False  # Source is not vertical

        with patch("pathlib.Path.exists", return_value=True):
            await run_tool_translate(
                job_id="test_9x16_explicit",
                in_path=Path("dummy_in.mp4"),
                target_language="Vietnamese",
                mode="captions_only",
                voice_id="",
                caption_style="viral",
                emoji_style="moderate",
                target_aspect="9:16",  # explicit: reframe to 9:16
            )

        # convert_aspect_ratio MUST have been called with target_aspect="9:16"!
        mock_convert.assert_called_once()
        self.assertEqual(mock_convert.call_args[1].get("target_aspect"), "9:16")


if __name__ == "__main__":
    unittest.main()
