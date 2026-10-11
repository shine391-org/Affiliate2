# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Guard context/group contract (plan VI, Task C).

Covers the eight authentic cues from the 2026-10-05 localization research
(plan VI section 1) with paired positive + mutation cases, so a fix that merely
suppresses the detector fails here. Fixtures live in
``fixtures/localization_guard/guard_context_cases.json``; they carry only the
cue strings, plus an explicit ``facts_certain`` / ``unresolved`` split so no
test certifies a translation that still needs audio adjudication.

The contract is backward compatible: ``check_translation_meaning`` and
``check_rewrite_meaning`` keep their positional signature, every new argument
is optional, and a caller that passes no context gets exactly the previous
single-segment behaviour. Ambiguous verdicts are reported as advisory
(severity ``uncertain``, action ``review``) instead of being silently dropped
or silently passed.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend.services.dubbing_rewrite_guard import (
    GuardContext,
    build_guard_context,
    check_rewrite_meaning,
    check_translation_group_meaning,
    check_translation_meaning,
)

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "localization_guard"
     / "guard_context_cases.json").read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in FIXTURES["cases"]}


def _kinds(case, *, target=None, context=None):
    target = case["target"] if target is None else target
    return check_translation_meaning(
        case["source"], target, case["source_language"], case["target_language"],
        glossary={}, context=context)


def _context_for(case):
    return GuardContext(
        source_before=case.get("source_context", ""),
        source_after=case.get("source_after_context", ""),
        target_before=case.get("target_context", ""),
        target_after=case.get("target_after_context", ""),
        group_id=case.get("group_id"),
    )


# ── 1. Every authentic positive is accepted, and stays accepted only with
#      the context its own cue justifies. ────────────────────────────────────

@pytest.mark.parametrize("case_id", sorted(CASES))
def test_authentic_positive_keeps_its_recorded_verdict(case_id):
    """expect_blocked is what the research recorded, not a blanket 'accept'."""
    case = CASES[case_id]
    if case.get("group"):
        issues = sorted({issue["kind"] for values in _group_verdicts(case, case["group"]).values()
                         for issue in values})
    else:
        issues = _kinds(case, context=_context_for(case))
    assert bool(issues) is bool(case["expect_blocked"]), (case_id, issues)
    if case["expect_blocked"]:
        # The recorded blocker must still be the reason, not some new accident.
        assert case["expect_kind"] in _kinds_of(issues), (case_id, issues)
    else:
        assert not _kinds_of(issues), (case_id, issues)


def _kinds_of(issues):
    return {issue["kind"] if isinstance(issue, dict) else issue for issue in issues}


def _group_verdicts(case, group_rows):
    return check_translation_group_meaning(
        [{"id": row["id"], "source_text": row["source"], "translated_text": row["target"]}
         for row in group_rows],
        case["source_language"], case["target_language"], glossary={})


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_each_mutation_is_still_blocked(case_id):
    case = CASES[case_id]
    context = _context_for(case)
    for mutation in case.get("mutations", []):
        target = mutation.get("target", case["target"])
        if "group" in mutation:
            issues = _group_issues(case, mutation["group"])
        else:
            mutation_context = context
            if "source_context" in mutation or "target_context" in mutation:
                mutation_context = GuardContext(
                    source_before=mutation.get("source_context", ""),
                    target_before=mutation.get("target_context", ""),
                )
            issues = _kinds(case, target=target, context=mutation_context)
        kinds = {issue["kind"] for issue in issues}
        if mutation["expect_blocked"]:
            assert mutation["expect_kind"] in kinds, (case_id, mutation["name"], issues)
        else:
            assert not issues, (case_id, mutation["name"], issues)


def _group_issues(case, group):
    """Run the whole semantic group and aggregate every member's issues.

    A mutation inside a group is reported on the member that carries the fact,
    so looking at one member alone would miss it."""
    segments = [
        {"id": row["id"], "source_text": row["source"], "translated_text": row["target"]}
        for row in group
    ]
    verdicts = check_translation_group_meaning(
        segments, case["source_language"], case["target_language"], glossary={})
    return [issue for issues in verdicts.values() for issue in issues]


@pytest.mark.parametrize(
    "case_id", [cid for cid, case in CASES.items() if case.get("group")])
def test_group_positive_is_accepted_for_every_member(case_id):
    case = CASES[case_id]
    verdicts = check_translation_group_meaning(
        [{"id": row["id"], "source_text": row["source"], "translated_text": row["target"]}
         for row in case["group"]],
        case["source_language"], case["target_language"], glossary={})
    assert verdicts == {row["id"]: [] for row in case["group"]}, verdicts


# ── 2. Generalized two-eyes classifier: body parts, not a fixture whitelist. ─

@pytest.mark.parametrize(
    "source,target,blocked",
    [
        ("看你的兩眼之間", "Xem giữa 2 mắt của bạn", False),
        ("看你的兩眼之間", "Xem giữa 3 mắt của bạn", True),
        ("看你的两只耳朵", "Nhìn 2 tai của bạn", False),
        ("看你的两只耳朵", "Nhìn 3 tai của bạn", True),
        ("看你的两手", "Nhìn 2 bàn tay của bạn", False),
        ("看你的两手", "Nhìn 4 bàn tay của bạn", True),
        ("兩眼", "2 mắt", False),
        ("兩眼", "1 mắt", True),
    ],
)
def test_two_or_more_is_a_count_without_a_classifier(source, target, blocked):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert bool(issues) is blocked, (source, target, issues)


@pytest.mark.parametrize(
    "source,target",
    [
        # 一 stays an indefinite article: these are not counts to protect.
        ("有一個女孩", "Có một cô gái", ),
        ("有一個女孩", "Có 2 cô gái"),
    ],
)
def test_single_article_is_still_a_count_when_counted(source, target):
    kinds = {issue["kind"] for issue in
             check_translation_meaning(source, target, "zh", "vi", glossary={})}
    assert ("quantity_lost" in kinds) is (target.startswith("Có 2")), (source, target, kinds)


# ── 3. Article asymmetry: counted object blocks, bare-noun article advises. ──

def test_counted_object_target_is_blocked_not_advised():
    case = CASES["f1a3f70c-seg-0008-article-vs-cardinal"]
    kinds = {issue["kind"] for issue in _kinds(case, context=_context_for(case))}
    assert "quantity_lost" in kinds, kinds


def test_bare_noun_article_target_is_advisory_not_blocked():
    case = CASES["f1a3f70c-seg-0066-superlative-article"]
    assert not _kinds(case, context=_context_for(case))
    advisory = check_translation_meaning(
        case["source"], case["target"], "zh", "vi", glossary={},
        include_advisories=True)
    kinds = {issue["kind"] for issue in advisory}
    assert "quantity_lost" not in kinds, advisory
    assert case["expect_advisory_kind"] in kinds, advisory


@pytest.mark.parametrize(
    "source,target,advisory",
    [
        ("其實是最正的一個和面", "thì thật ra là 1 khuôn mặt hài hòa nhất", True),
        ("其實是最正的一個和面", "thì thật ra là 3 khuôn mặt hài hòa nhất", False),
        ("其實是最正的一個和面", "thì thật ra là 1 cái bột hài hòa nhất", False),
    ],
)
def test_article_versus_count_is_decided_by_role_not_value(source, target, advisory):
    # advisory=True: a bare-noun article is ambiguous -> review, not reject.
    # advisory=False: a genuine count -> still a hard block.
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={},
                                       include_advisories=True)
    blocking = [issue["kind"] for issue in issues if issue["severity"] == "hard"]
    assert bool([i for i in issues if i["kind"] == "article_asymmetry"]) is advisory, issues
    assert bool(blocking) is (not advisory), (source, target, blocking)


# ── 4. Implicit age needs strict, matching context. ────────────────────────

def test_implicit_age_without_context_is_still_blocked():
    case = CASES["25babc2c-seg-0086-implicit-age"]
    kinds = {issue["kind"] for issue in _kinds(case, context=GuardContext())}
    assert "unit_lost" in kinds, kinds


@pytest.mark.parametrize(
    "source_before,target_before",
    [
        ("", ""),
        ("你好啊", "Chào bạn"),
        ("我们去看电影吧", "Chúng ta đi xem phim nhé"),
        ("多少钱一个", "Bao nhiêu tiền một cái"),
    ],
)
def test_unrelated_context_does_not_license_an_added_age_unit(source_before, target_before):
    case = CASES["25babc2c-seg-0086-implicit-age"]
    kinds = {issue["kind"] for issue in _kinds(
        case, context=GuardContext(source_before=source_before,
                                   target_before=target_before))}
    assert "unit_lost" in kinds, (source_before, kinds)


@pytest.mark.parametrize(
    "source_before", ["你多大", "你今年多大了", "你年龄多少"])
def test_age_question_context_licenses_only_the_age_unit(source_before):
    case = CASES["25babc2c-seg-0086-implicit-age"]
    assert not _kinds(case, context=GuardContext(source_before=source_before)), source_before
    kinds = {issue["kind"] for issue in _kinds(
        case, target="Tôi 23 kg rồi",
        context=GuardContext(source_before=source_before))}
    assert "unit_lost" in kinds, kinds


@pytest.mark.parametrize(
    "target_before",
    ["Bao nhiêu tuổi?", "Bao nhiêu tuổi anh vậy?", "Bạn bao nhiêu tuổi?"],
)
def test_target_window_cannot_license_its_own_added_age_unit(target_before):
    # The target is the text under test. A question that appears only there is
    # exactly the invention the guard exists to catch, so it must not license
    # the age unit it would justify.
    case = CASES["25babc2c-seg-0086-implicit-age"]
    kinds = {issue["kind"] for issue in _kinds(
        case, context=GuardContext(target_before=target_before))}
    assert "unit_lost" in kinds, (target_before, kinds)


def test_age_context_cannot_license_a_different_number():
    case = CASES["25babc2c-seg-0086-implicit-age"]
    kinds = {issue["kind"] for issue in _kinds(
        case, target="Tôi 24 tuổi rồi",
        context=GuardContext(source_before="你多大"))}
    assert "quantity_lost" in kinds, kinds


# ── 5. Rhetorical question: clause-final interrogatives. ───────────────────

@pytest.mark.parametrize(
    "source,target,blocked",
    [
        ("哪来的", "Ở đâu ra thế?", False),
        ("哪来的", "Ở đâu ra thế.", False),  # WH act survives missing ASR punctuation.
        ("哪来的", "Nó từ đó đến.", True),  # A real assertion replaces the question.
        ("這哪裡來的", "Cái này ở đâu ra thế?", False),
        ("哪裡來的", "Ở đâu ra đấy?", False),
    ],
)
def test_rhetorical_whence_is_interrogative(source, target, blocked):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert bool(issues) is blocked, (source, target, issues)


# ── 6. Sentence-level grouping of a question split across cues. ────────────

def _group(rows):
    return [{"id": row[0], "source_text": row[1], "translated_text": row[2]} for row in rows]


def test_question_split_over_two_cues_is_judged_as_one_sentence():
    verdicts = check_translation_group_meaning(
        _group([("seg-0025", "看你的鼻翼有沒有超過", "Xem cánh mũi của bạn có vượt quá"),
                ("seg-0026", "你的兩個瞳孔的內側", "phía trong 2 đồng tử của bạn không")]),
        "zh", "vi", glossary={})
    assert verdicts == {"seg-0025": [], "seg-0026": []}, verdicts


def test_dropping_the_group_question_is_blocked_on_the_cue_that_carries_it():
    # seg-0025 holds 有沒有; seg-0026 alone is not a question. Once the group
    # loses the interrogative, the member that carries it must be rejected — the
    # group cannot launder a real speech-act change.
    verdicts = check_translation_group_meaning(
        _group([("seg-0025", "看你的鼻翼有沒有超過", "Xem cánh mũi của bạn có vượt quá"),
                ("seg-0026", "你的兩個瞳孔的內側", "phía trong 2 đồng tử của bạn")]),
        "zh", "vi", glossary={})
    assert "question_changed" in {i["kind"] for i in verdicts["seg-0025"]}, verdicts


def test_group_does_not_excuse_a_count_mutation_inside_it():
    verdicts = check_translation_group_meaning(
        _group([("seg-0025", "看你的鼻翼有沒有超過", "Xem cánh mũi của bạn có vượt quá"),
                ("seg-0026", "你的兩個瞳孔的內側", "phía trong 3 đồng tử của bạn không")]),
        "zh", "vi", glossary={})
    assert "quantity_lost" in {issue["kind"] for issue in verdicts["seg-0026"]}, verdicts


def test_single_segment_call_is_unchanged_without_a_group():
    # Backward compatibility: the same cue judged alone still reports the
    # question change, because a half question has no group to complete it.
    issues = check_translation_meaning(
        "看你的鼻翼有沒有超過", "Xem cánh mũi của bạn có vượt quá", "zh", "vi", glossary={})
    assert "question_changed" in {issue["kind"] for issue in issues}, issues


def test_build_guard_context_exposes_neighbouring_cues():
    segments = _group([("a", "第一句", "câu một"), ("b", "第二句", "câu hai"),
                       ("c", "第三句", "câu ba")])
    context = build_guard_context(segments, 1)
    assert context.source_before == "第一句" and context.source_after == "第三句"
    assert context.target_before == "câu một" and context.target_after == "câu ba"
    assert build_guard_context(segments, 0).source_before == ""
    assert build_guard_context(segments, 2).source_after == ""


# ── 7. Negation scope: counts match, polarity moved between clauses. ──────

@pytest.mark.parametrize(
    "target,blocked",
    [
        ("Tôi mua táo, tôi không mua lê", True),
        ("Tôi không mua táo, tôi mua lê", False),
        ("Tôi không mua táo mà mua lê", False),
    ],
)
def test_negation_scope_flip_is_detected(target, blocked):
    issues = check_translation_meaning("我不买苹果，我买梨", target, "zh", "vi", glossary={})
    assert bool(issues) is blocked, (target, issues)


def test_negation_scope_issue_names_the_clause():
    issues = check_translation_meaning(
        "我不买苹果，我买梨", "Tôi mua táo, tôi không mua lê", "zh", "vi", glossary={},
        include_advisories=True)
    scope = [issue for issue in issues if issue["kind"] == "negation_scope_changed"]
    assert scope, issues
    assert scope[0]["severity"] == "hard" and scope[0]["action"] == "reject", scope


@pytest.mark.parametrize(
    "source,target",
    [
        ("我不喜欢这首歌", "Tôi không thích bài hát này"),
        ("我不想去", "Tôi không muốn đi"),
        ("不是红的", "Không phải màu đỏ"),
        ("别开门", "Đừng mở cửa"),
    ],
)
def test_ordinary_negation_translation_is_not_a_scope_change(source, target):
    assert not check_translation_meaning(source, target, "zh", "vi", glossary={})


# ── 8. Verdict contract: severity, action and backward compatibility. ──────

def test_every_issue_carries_severity_and_action():
    issues = check_translation_meaning(
        "买3个苹果", "Mua 2 quả táo", "zh", "vi", glossary={}, include_advisories=True)
    assert issues
    for issue in issues:
        assert issue["severity"] in {"hard", "uncertain"}, issue
        assert issue["action"] in {"reject", "repair", "review"}, issue


def test_advisories_are_opt_in_so_existing_callers_stay_strict():
    args = ("其實是最正的一個和面", "thì thật ra là 3 khuôn mặt hài hòa nhất", "zh", "vi")
    strict = check_translation_meaning(*args, glossary={})
    assert strict and all(issue["severity"] == "hard" for issue in strict)
    with_advisory = check_translation_meaning(*args, glossary={}, include_advisories=True)
    assert {issue["kind"] for issue in with_advisory} >= {issue["kind"] for issue in strict}


def test_glossary_and_protected_terms_still_apply_with_context():
    issues = check_translation_meaning(
        "月亮", "Trăng", "zh", "vi", glossary={"月亮": "trăng"},
        context=GuardContext(source_before="你多大"))
    assert not issues, issues
    issues = check_translation_meaning(
        "月亮", "Mặt trăng", "zh", "vi", glossary={}, protected_terms=["月亮"],
        context=GuardContext(source_before="你多大"))
    assert {issue["kind"] for issue in issues} == {"term_lost"}, issues


def test_rewrite_check_accepts_optional_context():
    # C7 rewrite path: no context is unchanged, context is accepted.
    assert not check_rewrite_meaning("Tôi 3 quả táo", "Tôi 3 quả táo", "vi")
    assert not check_rewrite_meaning(
        "Tôi 3 quả táo", "Tôi 3 quả táo", "vi", context=GuardContext(source_before="một"))
    kinds = {issue["kind"] for issue in
             check_rewrite_meaning("Tôi 3 quả táo", "Tôi 2 quả táo", "vi")}
    assert "quantity_lost" in kinds, kinds


def test_group_helper_requires_stable_ids():
    with pytest.raises(ValueError):
        check_translation_group_meaning(
            [{"id": "a", "source_text": "第一句", "translated_text": "câu một"},
             {"id": "a", "source_text": "第二句", "translated_text": "câu hai"}],
            "zh", "vi", glossary={})


def test_group_helper_keeps_accepted_members_independent():
    rows = _group([("a", "身高是175厘米", "Chiều cao là 175 cm"),
                   ("b", "喜欢吗", "Thích à")])
    verdicts = check_translation_group_meaning(rows, "zh", "vi", glossary={})
    assert verdicts["b"] == [], verdicts
    verdicts = check_translation_group_meaning(
        _group([("a", "身高是175厘米", "Chiều cao là 175 cm"),
                ("b", "身高是175厘米", "Chiều cao là 170 cm")]), "zh", "vi", glossary={})
    assert "quantity_lost" in {issue["kind"] for issue in verdicts["b"]}, verdicts
    assert verdicts["a"] == [], verdicts

# ── 8. A numeral inside a Chinese personal name is not a quantity. ───────
# Chinese writes 张三 ('Zhang San') without a separator, so a scanner that
# counts every numeral-plus-noun turns a name into a count and rejects a
# correct translation. These cases pin the generalized rule (surname + bare
# numeral, no measure word), not one fixture string.

@pytest.mark.parametrize(
    "source,target",
    [
        ("张三来了。", "Trương Tam đã đến."),
        ("张三", "Trương Tam"),
        ("李四买了三瓶水", "Lý Tứ mua 3 chai nước"),
        ("張三今年三十歲", "Trương Tam 30 tuổi năm nay"),
        ("老王五岁了", "Lão Vương 5 tuổi rồi"),
        ("老王五元", "Lão Vương 5 nhân dân tệ"),
        ("陈六说猫三只很可爱", "Trần Lục nói 3 con mèo rất dễ thương"),
    ],
)
def test_named_numeral_is_not_a_quantity(source, target):
    # include_advisories=True as well: the name case must not even be routed
    # to review, because there is no uncertainty about a correct translation.
    for advisories in (False, True):
        issues = check_translation_meaning(source, target, "zh", "vi", glossary={},
                                           include_advisories=advisories)
        assert issues == [], (source, target, advisories, issues)


@pytest.mark.parametrize(
    "source,target",
    [
        # A count next to the same name is still protected.
        ("张三来了。", "Trương Tam đã đến 3 lần."),
        ("李四买了三瓶水", "Lý Tứ mua 4 chai nước"),
        ("老王五岁了", "Lão Vương 6 tuổi rồi"),
        ("陈六说猫三只很可爱", "Trần Lục nói 4 con mèo rất dễ thương"),
        ("老王五元", "Lão Vương 6 nhân dân tệ"),
        # The name case is not a licence for any other change in the cue.
        ("张三带了三个苹果", "Trương Tam mang 2 quả táo"),
    ],
)
def test_named_numeral_does_not_licence_a_real_count_change(source, target):
    kinds = {issue["kind"] for issue in
             check_translation_meaning(source, target, "zh", "vi", glossary={})}
    assert "quantity_lost" in kinds, (source, target, kinds)


@pytest.mark.parametrize(
    "source,target",
    [
        # A surname-shaped prefix with no numeral is untouched.
        ("喜马拉雅山很高", "Dãy Himalaya rất cao"),
        # Neither is an unlisted-surname numeral: an unlisted surname keeps the
        # blocking (counted) reading rather than being silently accepted.
        ("元三来了。", "Nguyên Tam đã đến."),
    ],
)
def test_name_rule_is_scoped_to_listed_surnames(source, target):
    kinds = {issue["kind"] for issue in
             check_translation_meaning(source, target, "zh", "vi", glossary={})}
    assert kinds == set() or "quantity_lost" in kinds, (source, target, kinds)


def test_name_rule_leaves_ordinals_and_negation_intact():
    # The three preserved mutation families, in one cue that also contains a
    # named numeral: ordinal rank, cardinal count and negation scope.
    kinds = {issue["kind"] for issue in check_translation_meaning(
        "张三说第二次三瓶都没了", "Trương Tam nói lần thứ ba 3 chai đều hết",
        "zh", "vi", glossary={})}
    assert "ordinal_changed" in kinds, kinds
    kinds = {issue["kind"] for issue in check_translation_meaning(
        "张三说第二次三瓶都没了", "Trương Tam nói lần thứ hai 4 chai đều hết",
        "zh", "vi", glossary={})}
    assert "quantity_lost" in kinds, kinds
    kinds = {issue["kind"] for issue in check_translation_meaning(
        "张三不要苹果，我要梨", "Trương Tam mua táo, tôi không mua lê",
        "zh", "vi", glossary={})}
    assert "negation_scope_changed" in kinds, kinds


# ── 9. Lexical quantities are not counts (live guard diagnosis). ──────────
# 一點 / 一些 / 一陣子 / 千萬(不能) look numeric to a scanner but assert no
# countable value; the Vietnamese side renders them as '1 chút' / 'vài' /
# 'tuyệt đối', none of which is a number. The paired cases below pin both
# directions: the lexical reading must not block, and a real count in the same
# slot must still block.

@pytest.mark.parametrize(
    "source,target",
    [
        ("尤其是你的眉頭就剃掉一點", "Nhất là đầu lông mày, cạo bớt 1 chút"),
        ("適合出一些承擔的course", "hợp với vài phong cách trưởng thành"),
        ("因為要適合的寶寶都會有一點", "Vì bé hợp cằm đều sẽ có 1 chút"),
        ("男生千萬不能下巴後縮 會有吐氣感",
         "Con trai tuyệt đối không được cằm thụt, sẽ có cảm giác thở ra"),
        ("他千萬不要去", "Tuyệt đối đừng đến"),
        ("我有一點不舒服", "Tôi hơi không khỏe"),
    ],
)
def test_lexical_quantity_is_not_a_count(source, target):
    for advisories in (False, True):
        issues = check_translation_meaning(source, target, "zh", "vi", glossary={},
                                           include_advisories=advisories)
        assert issues == [], (source, target, advisories, issues)


def test_target_side_article_for_a_lexical_quantity_is_advisory_not_blocked():
    # 一會兒 asserts no count, but the translation spells its indefinite
    # article ('một lát'). Nothing is blocked; the role asymmetry is surfaced
    # for review rather than either passed silently or rejected.
    source, target = "你等一會兒再說", "Bạn đợi một lát nữa rồi hãy nói"
    assert not check_translation_meaning(source, target, "zh", "vi", glossary={})
    advisory = {issue["kind"] for issue in check_translation_meaning(
        source, target, "zh", "vi", glossary={}, include_advisories=True)}
    assert advisory == {"article_asymmetry"}, advisory


@pytest.mark.parametrize(
    "source,target",
    [
        # 千/萬 are real magnitudes in a count context: they must stay counts.
        ("他賺了一千萬", "Anh ấy kiếm được 9 triệu"),
        ("賣了一千萬台", "Bán được 10 triệu chiếc"),
        ("值一千萬台幣", "Trị 1 triệu đồng"),
        ("他等了兩小時", "Anh ấy đợi 30 phút"),
        # A degree slot is not a licence for a count to change next to it.
        ("我有一點不舒服", "Tôi không khỏe 3 ngày"),
        ("男生千萬不能下巴後縮", "Con trai tuyệt đối không được cằm thụt 2 lần"),
    ],
)
def test_real_count_next_to_a_lexical_quantity_still_blocks(source, target):
    kinds = {issue["kind"] for issue in
             check_translation_meaning(source, target, "zh", "vi", glossary={})}
    assert kinds & {"quantity_lost", "unit_lost"}, (source, target, kinds)


def test_bare_classifier_is_an_article_not_a_count():
    # 你是個月亮下巴: a bare 個 names a thing, which is the article role, so the
    # asymmetry is advisory rather than a hard block. A real count in the same
    # cue still blocks.
    source, target = "你是個月亮下巴 都下巴", "Bạn là cằm mặt trăng, cằm dài"
    assert not check_translation_meaning(source, target, "zh", "vi", glossary={})
    advisory = {issue["kind"] for issue in check_translation_meaning(
        source, target, "zh", "vi", glossary={}, include_advisories=True)}
    assert advisory == {"article_asymmetry"}, advisory
    kinds = {issue["kind"] for issue in
             check_translation_meaning("你是個月亮下巴還有三個", "Bạn là cằm mặt trăng còn 2 cái",
                                       "zh", "vi", glossary={})}
    assert "quantity_lost" in kinds, kinds


def test_decimal_is_not_masked_as_a_degree_expression():
    # 一点五 is 1.5, not 'a little five': the decimal rule still owns it.
    assert not check_translation_meaning("一點五的折扣", "Giảm 1.5 phần", "zh", "vi",
                                         glossary={})
    kinds = {issue["kind"] for issue in
             check_translation_meaning("一點五的折扣", "Giảm 2.5 phần", "zh", "vi",
                                       glossary={})}
    assert kinds & {"quantity_lost", "unit_lost"}, kinds
