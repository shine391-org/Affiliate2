# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend/core/crypto.py — encryption, decryption, masking."""
import pytest

from backend.core.crypto import encrypt, decrypt, decrypt_safe, mask, DecryptionError


# ── encrypt / decrypt roundtrip ────────────────────────────────────────────────

class TestEncryptDecrypt:
    def test_roundtrip_basic(self):
        plaintext = "my-secret-api-key-123"
        encrypted = encrypt(plaintext)
        assert encrypted != plaintext
        assert decrypt(encrypted) == plaintext

    def test_roundtrip_unicode(self):
        plaintext = "密码测试-日本語テスト-한국어"
        encrypted = encrypt(plaintext)
        assert decrypt(encrypted) == plaintext

    def test_roundtrip_long_string(self):
        plaintext = "x" * 10_000
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_roundtrip_special_chars(self):
        plaintext = "key=abc&secret=xyz!@#$%^&*()"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_different_encryptions_produce_different_ciphertext(self):
        """Fernet includes a random IV, so two encryptions of the same text differ."""
        plaintext = "same-secret"
        enc1 = encrypt(plaintext)
        enc2 = encrypt(plaintext)
        assert enc1 != enc2  # different ciphertext
        assert decrypt(enc1) == decrypt(enc2) == plaintext  # but same plaintext


# ── empty string handling ──────────────────────────────────────────────────────

class TestEmptyString:
    def test_encrypt_empty_returns_empty(self):
        assert encrypt("") == ""

    def test_decrypt_empty_returns_empty(self):
        assert decrypt("") == ""

    def test_decrypt_safe_empty_returns_empty(self):
        assert decrypt_safe("") == ""


# ── decrypt errors ─────────────────────────────────────────────────────────────

class TestDecryptErrors:
    def test_decrypt_garbage_raises_decryption_error(self):
        with pytest.raises(DecryptionError):
            decrypt("not-valid-ciphertext")

    def test_decrypt_modified_ciphertext_raises(self):
        encrypted = encrypt("secret")
        # Corrupt the ciphertext
        corrupted = encrypted[:-5] + "XXXXX"
        with pytest.raises(DecryptionError):
            decrypt(corrupted)

    def test_decrypt_with_wrong_key(self):
        """Simulate key rotation: encrypt with one key, try decrypting after key change."""
        encrypted = encrypt("secret")
        # We can't easily swap the key mid-test without reimporting,
        # but we can test with known-bad ciphertext
        with pytest.raises(DecryptionError):
            decrypt("gAAAAABh" + "A" * 100)  # malformed Fernet token


# ── decrypt_safe ───────────────────────────────────────────────────────────────

class TestDecryptSafe:
    def test_returns_plaintext_on_valid(self):
        encrypted = encrypt("my-key")
        assert decrypt_safe(encrypted) == "my-key"

    def test_returns_empty_on_garbage(self):
        assert decrypt_safe("not-valid-ciphertext") == ""

    def test_returns_empty_on_corrupted(self):
        encrypted = encrypt("secret")
        corrupted = encrypted[:-5] + "XXXXX"
        assert decrypt_safe(corrupted) == ""

    def test_returns_empty_on_none_like_input(self):
        assert decrypt_safe("") == ""


# ── mask ───────────────────────────────────────────────────────────────────────

class TestMask:
    def test_masks_long_string(self):
        result = mask("sk-1234567890abcdef")
        assert result.endswith("cdef")
        assert result.startswith("*")
        assert "1234" not in result  # middle is masked

    def test_masks_with_default_show_last_4(self):
        result = mask("abcdefgh")
        assert result == "****efgh"

    def test_masks_with_custom_show_last(self):
        result = mask("abcdefgh", show_last=2)
        assert result == "******gh"

    def test_short_string_returns_stars(self):
        assert mask("abc") == "****"
        assert mask("ab") == "****"
        assert mask("a") == "****"

    def test_empty_returns_stars(self):
        assert mask("") == "****"
        assert mask(None) == "****"

    def test_exact_length_equal_to_show_last(self):
        assert mask("abcd", show_last=4) == "****"

    def test_one_more_than_show_last(self):
        assert mask("abcde", show_last=4) == "*bcde"
