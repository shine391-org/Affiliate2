# SPDX-License-Identifier: AGPL-3.0-only
"""Rate-limit detection must not depend on the upstream account's locale.

The local FreeDeepseekAPI relay forwards the upstream provider's own error
text. A non-English upstream account reports rate limiting in its own language,
and the shared AI cooldown gate is only published when that error is recognised.
Missing the wording makes the caller retry blind against a saturated pool.
"""
from __future__ import annotations

import pytest

from backend.core.ai_provider import is_ai_rate_limit_error


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        self.headers: dict[str, str] = {}


def _err(status_code, body):
    exc = RuntimeError(str(body))
    exc.response = _Resp(status_code, body)
    exc.status_code = status_code
    exc.code = status_code
    return exc


RATE_LIMITED = [
    # English / Vietnamese markers that were already handled.
    ({"error": {"message": "Too many messages. Please retry later."}}, "english"),
    ({"error": {"message": "Gửi tin quá thường xuyên"}}, "vietnamese"),
    ({"error": {"message": "rate limit exceeded"}}, "english-rate-limit"),
    # Russian wording actually observed from a live DeepSeek upstream account.
    ({"error": {"message": "Слишком частые сообщения. Повторите попытку позже.",
                "type": "error", "agent": "dev-agent"}}, "russian-too-frequent"),
    ({"error": {"message": "Слишком много запросов"}}, "russian-too-many-requests"),
    ({"error": {"message": "Повторите попытку позже"}}, "russian-retry-later"),
]


@pytest.mark.parametrize("body,label", RATE_LIMITED)
def test_upstream_rate_limit_is_recognised(body, label):
    """A 502 carrying an upstream rate-limit message must publish the cooldown."""
    assert is_ai_rate_limit_error(_err(502, body)), f"{label} must be treated as rate limit"


NOT_RATE_LIMITED = [
    ({"error": {"message": "Internal server error"}}, "plain 500-class failure"),
    ({"error": {"message": "Слишком частые сообщения"}}, "russian wording under a 500"),
]


@pytest.mark.parametrize("body,label", NOT_RATE_LIMITED)
def test_unrelated_502_and_non_502_are_not_rate_limit(body, label):
    """Only a genuine 502 rate-limit message publishes the shared cooldown."""
    status = 500 if label.endswith("500") else 502
    if label == "russian wording under a 500":
        assert not is_ai_rate_limit_error(_err(500, body))
    else:
        assert not is_ai_rate_limit_error(_err(status, body)), label


def test_real_429_stays_rate_limit_without_body_inspection():
    exc = RuntimeError("429")
    exc.status_code = 429
    exc.code = 429
    assert is_ai_rate_limit_error(exc)
