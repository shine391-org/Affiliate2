# SPDX-License-Identifier: AGPL-3.0-only
"""Meaning-preservation guard for AI dubbing rewrites.

A duration rewrite may drop filler, not facts. These tests pin the code-level
validator that blocks negation/quantity/term/antonym loss per TARGET language.
"""
import pytest

from backend.services.dubbing_rewrite_guard import (
    check_rewrite_meaning,
)


# ── A7: the exact production failure from the sample job ──────────────────
def test_blocks_negation_loss_from_approved_text():
    """'Cái này thu tiền / Không miễn phí' -> 'Cái này thu tiền.' must be blocked.

    The slash presents two alternatives; the approved sentence carries a
    negative. Silently dropping it removes the distinguishing information.
    """
    before = "Cái này thu tiền / Không miễn phí"
    after = "Cái này thu tiền."
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert violations, "negation loss must be rejected"
    assert any(v["kind"] == "negation_lost" for v in violations)


def test_allows_removing_filler_without_losing_negation():
    """Dropping filler is the intended behaviour and must stay allowed."""
    violations = check_rewrite_meaning(
        "Thật ra thì chị có thể xem qua video này không ạ",
        "Chị xem video này không",
        "Vietnamese",
    )
    assert not violations


# ── quantities, units, ratios ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        ("Thu tiền 200k một tháng", "Thu tiền một tháng"),          # number
        ("Video dài 30 giây", "Video dài khá lâu"),                      # number
        ("Cân nặng 5kg", "Cân nặng nhẹ"),                                 # unit
        ("Hứa hoàn 100%", "Hứa hoàn"),                                    # percent
        ("Rồi lặp lại 3 lần", "Rồi lặp lại"),                            # counter
    ],
)
def test_blocks_number_or_unit_loss(before, after):
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert violations, f"quantity loss must be blocked: {before!r} -> {after!r}"
    assert any(v["kind"] in {"quantity_lost", "unit_lost"} for v in violations)


# ── defined terms (>=2 words) ─────────────────────────────────────────────
def test_blocks_defined_term_loss():
    violations = check_rewrite_meaning(
        "Ví dụ bố cục hai phần ba của bạn",
        "Ví dụ như bố cục này của bạn",
        "Vietnamese",
    )
    assert violations
    assert any(v["kind"] == "term_lost" for v in violations)


def test_allows_dropping_a_generic_filler_bigram():
    """'bố cục này' is not a defined term; losing it is legitimate compression."""
    violations = check_rewrite_meaning(
        "Bố cục này gọi là bố cục hai phần ba",
        "Bố cục hai phần ba",
        "Vietnamese",
    )
    assert not violations


# ── antonyms / reversed meaning ───────────────────────────────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        ("Muốn chụp không?", "Muốn quay không?"),        # chụp <-> quay
        ("Nói trước cho tôi nghe", "Nói sau cho tôi nghe"),  # trước <-> sau
        ("Giá tăng rồi", "Giá giảm rồi"),                # tăng <-> giảm
    ],
)
def test_blocks_antonym_flip(before, after):
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert violations
    assert any(v["kind"] == "antonym_flip" for v in violations)


# ── target-language coverage (must NOT hardcode Vietnamese) ───────────────
@pytest.mark.parametrize(
    "language,before,after,kind",
    [
        ("English", "This is not free", "This is free.", "negation_lost"),
        ("English", "It costs 30 dollars", "It costs some money", "quantity_lost"),
        ("Spanish", "Esto no es gratis", "Esto es gratis.", "negation_lost"),
        ("French", "Ce n'est pas gratuit", "C'est gratuit.", "negation_lost"),
        ("German", "Das ist nicht kostenlos", "Das ist kostenlos.", "negation_lost"),
        ("Japanese", "これは無料ではありません", "これは無料です", "negation_lost"),
        ("Korean", "무료가 아닙니다", "무료입니다", "negation_lost"),
        ("Chinese", "这不是免费的", "这是免费的", "negation_lost"),
    ],
)
def test_negation_guard_is_language_scoped(language, before, after, kind):
    violations = check_rewrite_meaning(before, after, language)
    assert violations, f"{language} negation loss must be blocked"
    assert any(v["kind"] == kind for v in violations)


def test_unknown_language_still_guards_quantities():
    """An unmapped target language must still get structural protection."""
    violations = check_rewrite_meaning(
        "The clip is 45 seconds long", "The clip is short", "Klingon"
    )
    assert violations
    assert any(v["kind"] in {"quantity_lost", "unit_lost"} for v in violations)


def test_identical_text_has_no_violations():
    assert not check_rewrite_meaning("Xin chào các bạn", "Xin chào các bạn", "Vietnamese")


def test_empty_or_blank_inputs_are_safe():
    assert not check_rewrite_meaning("", "", "Vietnamese")
    assert not check_rewrite_meaning("Xin chào", "   ", "Vietnamese")


def test_cjk_text_without_spaces_still_detects_negation():
    """CJK has no whitespace; tokenisation must not silently no-op."""
    violations = check_rewrite_meaning(
        "这不是免费的", "这是免费的", "Chinese"
    )
    assert violations