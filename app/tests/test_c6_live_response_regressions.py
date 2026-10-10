# SPDX-License-Identifier: AGPL-3.0-only
"""Authentic DeepSeek false positives and bounded grammar variants.

Only the leaf AI reply is recorded in the pipeline test; C6 validation,
checkpoint writes and strict/standard policy execute normally.
"""
import json
from types import SimpleNamespace

import pytest

from backend.core.localization import LocalizationSegment, LocalizationTranscript, fast_localization_profile
from backend.services import localization_service as service
from backend.services.dubbing_rewrite_guard import check_translation_meaning


EQUIVALENT = [
    ("我不是老师，每个视频有三个步骤。", "Tôi không phải là giáo viên, mỗi video có ba bước."),
    ("有两步", "Có hai bước"),
    ("共有五步", "Có tất cả năm bước"),
    ("这里有两章", "Ở đây có hai chương"),
    ("共有五页", "Có tất cả năm trang"),
    ("一系列作品", "Một loạt tác phẩm"),
    ("两系列作品", "Hai loạt tác phẩm"),
    ("五系列作品", "Năm loạt tác phẩm"),
    ("一劳永逸的自动化办公", "Tự động hóa văn phòng một lần là xong"),
    ("一劳永逸", "Một lần xong"),
    ("一勞永逸", "Làm một lần là xong"),
    ("也有审视多事的数据分析和一劳永逸的自动化办公等等一系列的作品", "cũng có phân tích dữ liệu chuyên sâu và tự động hóa văn phòng một lần là xong cùng một loạt tác phẩm khác"),
    ("有无孔不入的网络爬虫", "có những con bot thu thập dữ liệu web không chỗ nào không lọt vào"),
    ("广告无孔不入", "Quảng cáo không nơi nào không xuất hiện"),
    ("廣告無孔不入", "Quảng cáo chẳng nơi nào mà chẳng xuất hiện"),
    ("广告无孔不入", "Quảng cáo không đâu không lọt vào"),
    ("广告无孔不入", "Quảng cáo chẳng đâu mà chẳng xuất hiện"),
    ("廣告無孔不入", "Quảng cáo không nơi nào mà không xuất hiện"),
    ("知道吗我不在居中了", "Biết không, tôi không ở chính giữa nữa"),
    ("你明白吗我不来了", "Bạn hiểu chứ, tôi không đến nữa"),
    ("你懂嗎他不來了", "Bạn hiểu chứ, anh ấy không đến nữa"),
]


@pytest.mark.parametrize("source,target", EQUIVALENT)
def test_equivalent_live_reply_and_grammar_variants(source, target):
    assert not check_translation_meaning(source, target, "zh", "vi", glossary={})


@pytest.mark.parametrize("source,target,kind", [
    ("有三步", "Có hai bước", "quantity_lost"),
    ("有两章", "Có ba chương", "quantity_lost"),
    ("有五页", "Có bốn trang", "quantity_lost"),
    ("两系列作品", "Ba loạt tác phẩm", "quantity_lost"),
    ("一劳永逸只用五公斤", "Một lần là xong, chỉ dùng sáu kg", "quantity_lost"),
    ("一劳永逸不要修改", "Một lần là xong, hãy sửa", "negation_lost"),
    ("不是无孔不入", "Không chỗ nào không lọt vào", "negation_lost"),
    ("无孔不入", "Không phải không chỗ nào không lọt vào", "negation_added"),
    ("不是无孔不入", "Không đâu không lọt vào", "negation_lost"),
    ("无孔不入", "Không phải không đâu không lọt vào", "negation_added"),
    ("知道吗我不在居中了", "Biết không, tôi ở chính giữa nữa", "negation_lost"),
    ("知道吗我不在居中了", "Tôi không ở chính giữa nữa", "question_changed"),
    ("吗啡有效", "Morphin có tác dụng", None),
])
def test_new_grammar_does_not_remove_real_invariants(source, target, kind):
    issues = check_translation_meaning(source, target, "zh", "vi", glossary={})
    if kind is None:
        assert not issues
    else:
        assert kind in {issue["kind"] for issue in issues}


@pytest.mark.asyncio
@pytest.mark.parametrize("qc", ["standard", "strict"])
async def test_canonical_c6_persists_equivalent_replies_without_repair(monkeypatch, tmp_path, qc):
    class RecordedAI:
        provider = SimpleNamespace(value="compatible")
        model = "recorded-deepseek-chat"
        connection_name = "recorded"
        max_retries = 0
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            requested = kwargs["messages"][0]["content"].split("SEGMENTS:\n", 1)[1]
            items, _ = json.JSONDecoder().raw_decode(requested.lstrip())
            return json.dumps([{"id": item["id"], "text": EQUIVALENT[int(item["id"][1:])][1]} for item in items], ensure_ascii=False)

    ai = RecordedAI()
    monkeypatch.setattr(service, "_profile_ai_route_clients_compat", lambda *args: [ai])
    tx = LocalizationTranscript(language="zh", segments=[
        LocalizationSegment(id=f"s{i}", source_text=source, source_start_ms=i * 2000, source_end_ms=i * 2000 + 1000)
        for i, (source, _) in enumerate(EQUIVALENT)
    ])
    result = await service.translate_transcript(tx, target_language="vi",
        profile=fast_localization_profile().model_copy(update={"qc_profile": qc}),
        user_settings=None, for_dub=True, checkpoint_dir=tmp_path)
    assert all(s.translation_status == "success" for s in result.segments)
    assert [s.translated_text for s in result.segments] == [target for _, target in EQUIVALENT]
    assert ai.calls == 2  # Canonical batches of at most 12.
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert sum(len(batch["items"]) for batch in checkpoint["batches"].values()) == len(EQUIVALENT)
    assert all(not batch["pending_ids"] for batch in checkpoint["batches"].values())
    assert checkpoint["meaning_guard_version"] == 13
    # Current valid checkpoints are reused, but previous guards cannot carry
    # old acceptances across the changed semantic contract.
    await service.translate_transcript(tx, target_language="vi",
        profile=fast_localization_profile().model_copy(update={"qc_profile": qc}),
        user_settings=None, for_dub=True, checkpoint_dir=tmp_path)
    assert ai.calls == 2
    for old_version, expected_calls in ((5, 4), (7, 6), (8, 8), (12, 10)):
        checkpoint["meaning_guard_version"] = old_version
        (tmp_path / "checkpoint.json").write_text(json.dumps(checkpoint))
        await service.translate_transcript(tx, target_language="vi",
            profile=fast_localization_profile().model_copy(update={"qc_profile": qc}),
            user_settings=None, for_dub=True, checkpoint_dir=tmp_path)
        assert ai.calls == expected_calls
        checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert checkpoint["meaning_guard_version"] == 13
