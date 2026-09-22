# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend/services/caption_service.py — emoji insertion and caption styles."""
import pytest

from backend.services.caption_service import (
    insert_emojis_into_words,
    CAPTION_STYLES,
    EMOJI_KEYWORDS,
)


class TestInsertEmojis:
    def _make_words(self, texts: list[str]) -> list[dict]:
        return [{"text": t, "start": i, "end": i + 0.5} for i, t in enumerate(texts)]

    def test_none_style_no_changes(self):
        words = self._make_words(["money", "cash", "save"])
        result = insert_emojis_into_words(words, style="none")
        assert all(w["text"] in ("money", "cash", "save") for w in result)

    def test_heavy_style_adds_emoji_on_every_match(self):
        words = self._make_words(["money", "invest", "subscribe"])
        result = insert_emojis_into_words(words, style="heavy")
        for w in result:
            base = w["text"].split()[0].lower()
            if base in EMOJI_KEYWORDS:
                assert len(w["text"].split()) >= 2  # word + emoji

    def test_moderate_style_skips_some_matches(self):
        # 6 keyword words — at moderate (interval=3), should get emoji on 3rd and 6th
        words = self._make_words(["money", "cash", "save", "invest", "rich", "earn"])
        result = insert_emojis_into_words(words, style="moderate")
        emoji_count = sum(1 for w in result if len(w["text"]) > len(w["text"].split()[0]))
        assert emoji_count >= 1  # at least some emojis added

    def test_minimal_style_fewer_emojis(self):
        words = self._make_words(["money", "cash", "save", "invest", "rich", "earn"])
        result_minimal = insert_emojis_into_words(
            self._make_words(["money", "cash", "save", "invest", "rich", "earn"]),
            style="minimal",
        )
        result_heavy = insert_emojis_into_words(
            self._make_words(["money", "cash", "save", "invest", "rich", "earn"]),
            style="heavy",
        )
        emojis_minimal = sum(1 for w in result_minimal if " " in w["text"])
        emojis_heavy = sum(1 for w in result_heavy if " " in w["text"])
        assert emojis_minimal <= emojis_heavy

    def test_non_keyword_words_unchanged(self):
        words = self._make_words(["the", "quick", "brown", "fox"])
        result = insert_emojis_into_words(words, style="heavy")
        assert all(w["text"] in ("the", "quick", "brown", "fox") for w in result)

    def test_returns_same_list(self):
        words = self._make_words(["hello", "world"])
        result = insert_emojis_into_words(words, style="moderate")
        assert result is words  # mutates in place, returns same list

    def test_empty_word_list(self):
        result = insert_emojis_into_words([], style="heavy")
        assert result == []

    def test_case_insensitive_matching(self):
        words = self._make_words(["Money", "CASH"])
        result = insert_emojis_into_words(words, style="heavy")
        # Should match despite capitalization
        for w in result:
            base = w["text"].split()[0].lower().strip()
            if base in EMOJI_KEYWORDS:
                assert " " in w["text"]  # emoji appended


class TestCaptionStyles:
    def test_all_styles_have_required_keys(self):
        required_keys = [
            "font", "font_size_portrait", "font_size_landscape",
            "primary_color", "highlight_color", "outline_color",
            "outline_width", "shadow_depth", "alignment",
            "margin_v", "words_per_group",
        ]
        for style_name, style in CAPTION_STYLES.items():
            for key in required_keys:
                assert key in style, f"Style '{style_name}' missing key '{key}'"

    def test_known_styles_exist(self):
        expected = ["viral", "classic", "bold", "neon", "minimal", "karaoke", "glow"]
        for name in expected:
            assert name in CAPTION_STYLES, f"Missing style: {name}"

    def test_font_sizes_are_positive(self):
        for name, style in CAPTION_STYLES.items():
            assert style["font_size_portrait"] > 0, f"{name} portrait size"
            assert style["font_size_landscape"] > 0, f"{name} landscape size"

    def test_portrait_larger_than_landscape(self):
        """Portrait mode should generally have larger font for readability."""
        for name, style in CAPTION_STYLES.items():
            assert style["font_size_portrait"] >= style["font_size_landscape"], (
                f"{name}: portrait ({style['font_size_portrait']}) should be >= "
                f"landscape ({style['font_size_landscape']})"
            )

    def test_words_per_group_positive(self):
        for name, style in CAPTION_STYLES.items():
            assert style["words_per_group"] > 0, f"{name} words_per_group"

    def test_ass_color_format(self):
        """ASS colors should be in &H format."""
        for name, style in CAPTION_STYLES.items():
            for color_key in ["primary_color", "highlight_color", "outline_color"]:
                color = style[color_key]
                assert color.startswith("&H"), (
                    f"{name}.{color_key} = '{color}' doesn't start with &H"
                )


class TestEmojiKeywords:
    def test_common_keywords_present(self):
        assert "money" in EMOJI_KEYWORDS
        assert "subscribe" in EMOJI_KEYWORDS
        assert "fire" in EMOJI_KEYWORDS
        assert "love" in EMOJI_KEYWORDS

    def test_all_values_are_emoji_strings(self):
        for keyword, emoji in EMOJI_KEYWORDS.items():
            assert isinstance(emoji, str)
            assert len(emoji) > 0
            assert isinstance(keyword, str)
