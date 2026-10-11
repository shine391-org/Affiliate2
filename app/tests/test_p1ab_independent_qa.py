# SPDX-License-Identifier: AGPL-3.0-only
"""P1-A / P1-B independent regression tests.

Written RED first against HEAD 6eba93c on feat/localization-fast-mode.
Each test states the observed defect and the required behaviour.
"""
from __future__ import annotations

import json

import pytest

from backend.core.localization import LocalizationProfile, LocalizationSegment
from backend.services import localization_service as ls
from backend.services.dubbing_rewrite_guard import check_translation_meaning


# ── P1-A: semantic guard FALSE POSITIVES already failing at HEAD ───────────
@pytest.mark.parametrize(
    "source,target,why",
    [
        # 一 + 点 is the degree expression 'a little bit', never a count.
        ("再高一点点", "Cao thêm một tí nữa", "degree expression 一点点"),
        # 一劳永逸 is the fixed idiom 'once and for all'; no counted object.
        ("一劳永逸", "Giải quyết triệt để", "idiom 一劳永逸"),
        ("一勞永逸", "Giải quyết triệt để", "traditional idiom 一勞永逸"),
        # 第N个 : the ordinal slot owns the rank; the surviving 个 is the
        # article, not an extra counted object.
        ("第二个视频", "Video thứ hai", "ordinal + article 第二个"),
        # 星期一 is a WEEKDAY ordinal; Vietnamese renders it thứ hai.
        ("星期一", "Thứ hai", "weekday homograph 星期一"),
        ("星期二", "Thứ ba", "weekday homograph 星期二"),
        # 吗 inside a comma-joined clause is still a question; the Vietnamese
        # 'không' tag is its question form and must not be read as negation.
        ("你知道吗，继续看", "Bạn biết không, xem tiếp nhé", "吗 comma question"),
    ],
)
def test_p1a_equivalent_translation_is_not_rejected(source, target, why):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert issues == [], f"{why}: {source!r} -> {target!r} wrongly rejected: {issues}"


@pytest.mark.parametrize(
    "source,target",
    [
        ("三瓶水", "năm chai nước"),          # real count changed
        ("五本书", "ba quyển sách"),          # real count changed
        ("高一点五米", "Cao 2 mét"),          # real decimal measurement
    ],
)
def test_p1a_real_corruption_still_blocked(source, target):
    """The fix must not weaken the guard: genuine changes stay rejected."""
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    assert issues, f"{source!r} -> {target!r} must stay blocked"


# ── P1-B: fallback chain abandoned after malformed response ────────────────
def _profile(**u):
    base = LocalizationProfile(
        id="qa", name="QA", target_aspect="source", glossary={},
        translation_instructions="", audio_mode="fast_duck", alignment_mode="off",
        diarization_mode="off", video_retime_mode="off",
    )
    return LocalizationProfile.model_validate({**base.model_dump(), **u})


def _batch():
    # s0 carries a negation; dropping it is a semantic violation.
    return [
        LocalizationSegment(id="s0", source_text="这不是免费的。",
                            source_start_ms=0, source_end_ms=1000),
        LocalizationSegment(id="s1", source_text="你好",
                            source_start_ms=1000, source_end_ms=2000),
    ]


class _Client:
    def __init__(self, name, behaviour):
        self.connection_name = name
        self.provider = type("P", (), {"value": "compatible"})()
        self.model = name
        self.max_retries = None
        self.behaviour = behaviour
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        content = messages[0]["content"]
        ids = json.loads(content.split("SEGMENTS:\n", 1)[1].split("\n\nReturn ONLY", 1)[0])
        return self.behaviour(ids, content)


def _payload(ids, *, good=True):
    texts = {
        "s0": "Đây không phải miễn phí." if good else "Đây là miễn phí.",
        "s1": "Xin chào",
    }
    return json.dumps([{"id": x["id"], "text": texts[x["id"]]} for x in ids],
                      ensure_ascii=False)


@pytest.mark.asyncio
async def test_p1b_healthy_fallback_is_attempted_after_malformed_route():
    """Primary returns a semantically invalid translation and keeps returning it
    after repair. Fallback A returns malformed JSON. Fallback B is healthy.

    Required: B is attempted and its translation is accepted; the segment is
    NOT abandoned and no accepted sibling is disturbed.
    """
    primary = _Client("primary", lambda ids, c: _payload(ids, good=False))
    fallback_a = _Client("fallbackA", lambda ids, c: '{"broken": json,,,}')
    fallback_b = _Client("fallbackB", lambda ids, c: _payload(ids, good=True))
    state = ls._AIStageRouteState([primary, fallback_a, fallback_b])

    texts, failed = await ls._translate_batch_resilient(
        primary, _batch(), route_state=state,
        target_language="Vietnamese", source_language="zh", context="ctx",
        profile=_profile(glossary={}, translation_retry_attempts=1,
                         translation_attempt_timeout_s=10,
                         translation_total_timeout_s=30),
        for_dub=True, fallback_ai=fallback_a)

    assert texts[0] == "Đây không phải miễn phí.", (
        f"healthy fallback B must be used, got {texts[0]!r}")
    assert texts[1] == "Xin chào"
    assert failed == set(), f"no segment may be abandoned, failed={failed}"
    assert fallback_b.calls >= 1, "fallback B must actually be dispatched"
