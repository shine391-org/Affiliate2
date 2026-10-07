# SPDX-License-Identifier: AGPL-3.0-only
"""C7 semantic-role/relation guards for AI dubbing rewrites.

These tests pin the code-level validator that blocks:
1. Actor/object reversal (e.g., 'tôi chụp em' -> 'em chụp tôi')
2. Explicit role loss (e.g., 'tôi chụp em' -> 'chụp' with both roles dropped)
3. Explicit relation loss (e.g., 'tên là' -> 'là')

While preserving:
- Contextual pronoun ellipsis ('Em có thích không' -> 'Thích không')
- Actor/recipient-preserving compression ('tôi qua đây đánh dấu cho em' -> 'tôi đánh dấu cho em')

The guard must generalize beyond these samples, not whitelist exact strings.
"""
import pytest

from backend.services.dubbing_rewrite_guard import (
    check_rewrite_meaning,
)


# ── 1. Actor/object reversal must be blocked ───────────────────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        # Exact production failure
        ("Vậy tôi chụp em nhé", "Em chụp tôi nhé"),
        # Generalized: different pronouns, same reversal pattern
        ("Tôi nhìn anh", "Anh nhìn tôi"),
        ("Em gọi tôi", "Tôi gọi em"),
        ("Chị đưa bạn", "Bạn đưa chị"),
        # Reversal with additional context
        ("Hôm qua tôi gặp anh ở công viên", "Hôm qua anh gặp tôi ở công viên"),
    ],
)
def test_blocks_actor_object_reversal(before, after):
    """Actor/object reversal changes who does what to whom — must be blocked."""
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert violations, f"actor/object reversal must be blocked: {before!r} -> {after!r}"
    assert any(v["kind"] == "role_reversal" for v in violations), violations


# ── 2. Explicit role loss must be blocked ──────────────────────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        # Exact production failure: both actor and object dropped
        ("Vậy tôi chụp em nhé", "Vậy chụp nhé"),
        # Generalized: different verbs, same loss pattern
        ("Tôi nhìn anh", "Nhìn"),
        ("Em gọi tôi", "Gọi"),
        # Loss with surrounding context preserved
        ("Hôm qua tôi gặp anh ở công viên", "Hôm qua gặp ở công viên"),
    ],
)
def test_blocks_explicit_role_loss(before, after):
    """Dropping both actor and object removes who-does-what — must be blocked."""
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert violations, f"explicit role loss must be blocked: {before!r} -> {after!r}"
    assert any(v["kind"] == "role_loss" for v in violations), violations


# ── 3. Explicit relation loss must be blocked ──────────────────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        # Exact production failure: naming relation lost
        ("Tên là Vũ", "Là Vũ"),
        # Generalized: different relation markers
        ("Tên gọi là Minh", "Là Minh"),
        ("Tên của tôi là An", "Của tôi là An"),
        # Relation loss with context
        ("Cái này tên là bút", "Cái này là bút"),
    ],
)
def test_blocks_explicit_relation_loss(before, after):
    """Losing an explicit relation marker (e.g., 'tên là') removes the naming/assertion — must be blocked."""
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert violations, f"explicit relation loss must be blocked: {before!r} -> {after!r}"
    assert any(v["kind"] == "relation_loss" for v in violations), violations


# ── 4. Contextual pronoun ellipsis must be allowed ─────────────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        # Exact positive: subject ellipsis, no object to lose
        ("Em có thích không", "Thích không"),
        # Generalized: different subjects, same ellipsis pattern
        ("Anh có muốn đi không", "Muốn đi không"),
        ("Chị có biết không", "Biết không"),
        # Ellipsis with additional context
        ("Em có thích bài này không", "Thích bài này không"),
    ],
)
def test_allows_contextual_pronoun_ellipsis(before, after):
    """Subject pronoun ellipsis without object loss is legitimate compression."""
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert not violations, f"contextual ellipsis must be allowed: {before!r} -> {after!r}, got {violations}"


# ── 5. Actor/recipient-preserving compression must be allowed ──────────────
@pytest.mark.parametrize(
    "before,after",
    [
        # Exact positive: filler dropped, actor and recipient preserved
        ("Tôi qua đây đánh dấu cho em", "Tôi đánh dấu cho em"),
        # Generalized: different fillers, same preservation pattern
        ("Tôi lấy bút viết cho anh", "Tôi viết cho anh"),
        ("Em lấy sách đưa cho tôi", "Em đưa cho tôi"),
        # Multiple fillers dropped
        ("Tôi qua đây lấy bút viết cho em", "Tôi viết cho em"),
    ],
)
def test_allows_actor_recipient_preserving_compression(before, after):
    """Dropping filler while preserving actor and recipient is legitimate."""
    violations = check_rewrite_meaning(before, after, "Vietnamese")
    assert not violations, f"actor/recipient-preserving compression must be allowed: {before!r} -> {after!r}, got {violations}"


# ── 6. Guard must not over-trigger on legitimate changes ───────────────────
@pytest.mark.parametrize(
    "before,after",
    [
        ("Tôi đi", "Đi"),
        ("Tôi và em thích sách", "Cả hai thích sách"),
        ("Tôi gọi em; chị giúp anh", "Chị giúp anh; tôi gọi em"),
        ("Tôi nghe anh; anh nhìn tôi", "Anh nhìn tôi; tôi nghe anh"),
        ("Tên gọi là Minh", "Tên là Minh"),
        ("Tên gọi là Minh", "Gọi là Minh"),
        ("Có tên gọi là An", "Tên là An"),
    ],
)
def test_guard_does_not_over_trigger(before, after):
    """Guard must not block legitimate changes."""
    assert not check_rewrite_meaning(before, after, "Vietnamese")


# ── 7. Guard must work without context (production acceptance path) ────────
def test_guard_works_without_context():
    """Production acceptance calls check_rewrite_meaning without context."""
    # These must be blocked even without context
    assert check_rewrite_meaning("Vậy tôi chụp em nhé", "Em chụp tôi nhé", "Vietnamese")
    assert check_rewrite_meaning("Vậy tôi chụp em nhé", "Vậy chụp nhé", "Vietnamese")
    assert check_rewrite_meaning("Tên là Vũ", "Là Vũ", "Vietnamese")

    # These must be allowed even without context
    assert not check_rewrite_meaning("Em có thích không", "Thích không", "Vietnamese")
    assert not check_rewrite_meaning("Tôi qua đây đánh dấu cho em", "Tôi đánh dấu cho em", "Vietnamese")


# ── 8. Guard must be conservative: uncertain cases are advisory ────────────
def test_uncertain_cases_are_advisory_not_blocking():
    """When the guard cannot confidently classify, it should advise review, not block."""
    issues = check_rewrite_meaning("Tôi chụp em", "Chụp em", "Vietnamese", include_advisories=True)
    assert any(i["kind"] == "role_ellipsis" and i["action"] == "review" and i["severity"] == "uncertain" for i in issues)
    assert not check_rewrite_meaning("Tôi chụp em", "Chụp em", "Vietnamese")


# ── 9. Guard must preserve existing invariants ─────────────────────────────
def test_existing_invariants_still_work():
    """New guard must not break existing quantity/negation/question/protected guards."""
    # Negation
    assert check_rewrite_meaning("Không miễn phí", "Miễn phí", "Vietnamese")
    # Quantity
    assert check_rewrite_meaning("30 giây", "Vài giây", "Vietnamese")
    # Question
    assert check_rewrite_meaning("Có không?", "Có", "Vietnamese")
    # Protected term
    assert check_rewrite_meaning("Tên là Vũ", "Là Vũ", "Vietnamese", protected_terms=["Vũ"])


# ── 10. Guard must generalize beyond exact samples ─────────────────────────
def test_guard_generalizes_beyond_samples():
    """Guard must catch variants, not just the exact production samples."""
    # Different pronouns
    assert check_rewrite_meaning("Anh nhìn chị", "Chị nhìn anh", "Vietnamese")
    # Different verbs
    assert check_rewrite_meaning("Tôi gọi em", "Gọi", "Vietnamese")
    # Different relation markers
    assert check_rewrite_meaning("Tên gọi là An", "Là An", "Vietnamese")

    # Different ellipsis patterns
    assert not check_rewrite_meaning("Bạn có biết không", "Biết không", "Vietnamese")
    # Different compression patterns
    assert not check_rewrite_meaning("Tôi lấy bút viết cho anh", "Tôi viết cho anh", "Vietnamese")


@pytest.mark.parametrize('before,after', [
    ('Tôi chụp em.', 'Em chụp tôi.'),
    ('Tôi gọi anh!', 'Anh gọi tôi!'),
    ('Họ giúp chúng tôi.', 'Chúng tôi giúp họ.'),
    ('Tôi nhìn anh, đúng rồi.', 'Anh nhìn tôi, đúng rồi.'),
])
def test_punctuation_and_multiword_participants_cannot_hide_reversal(before,after):
    issues=check_rewrite_meaning(before,after,'Vietnamese')
    assert any(i['kind']=='role_reversal' for i in issues),issues
