# SPDX-License-Identifier: AGPL-3.0-only
"""False positives and state regressions reproduced during the branch review."""

import json
from types import SimpleNamespace
import pytest
from backend.core.localization import LocalizationSegment, LocalizationTranscript, fast_localization_profile
from backend.services import localization_service as svc
from backend.services.dubbing_rewrite_guard import check_translation_meaning
from backend.services.localization_stage_service import LocalizationRun
from backend.config import settings
from backend.api import localization as api
from tests.test_localization_measured_speakers import transcript, mock_dubbing
from tests.test_localization_voice_workflow import source_voices


@pytest.mark.parametrize(
    "source,target,sl,tl",
    [
        ("Do not eat.", "Ne mange pas.", "en", "fr"),
        ("I do not agree.", "Je ne suis pas d’accord.", "en", "fr"),
        ("He does not come.", "Il ne vient pas.", "en", "fr"),
        ("我的表不准。", "Đồng hồ của tôi chạy sai.", "zh", "vi"),
        ("这个时钟不准。", "Chiếc đồng hồ này chạy sai.", "zh", "vi"),
        ("温度不准。", "Nhiệt độ đo sai.", "zh", "vi"),
        ("下一步继续。", "Continue with the next step.", "zh", "en"),
        ("第一步加热。", "Heat in the first step.", "zh", "en"),
        ("Next step, heat it.", "Bước tiếp theo, đun nóng.", "en", "vi"),
        ("第二天他回来。", "Ngày thứ hai anh ấy trở lại.", "zh", "vi"),
        ("第二个月他回来。", "Tháng thứ hai anh ấy trở lại.", "zh", "vi"),
        ("第三年他回来。", "Năm thứ ba anh ấy trở lại.", "zh", "vi"),
        ("下一天继续。", "Continue the next day.", "zh", "en"),
        ("第二章继续。", "Continue in the second chapter.", "zh", "en"),
        ("第三步继续。", "Continue with the 3rd step.", "zh", "en"),
        ("First step, heat it.", "Bước đầu tiên, đun nóng.", "en", "vi"),
        ("Second chapter.", "Chương thứ hai.", "en", "vi"),
        ("下一年继续。", "Tiếp tục năm sau.", "zh", "vi"),
        ("Never eat it.", "Ne le mange jamais.", "en", "fr"),
        ("There is nothing here.", "Il n’y a rien ici.", "en", "fr"),
        ("第二周继续。", "Tiếp tục tuần thứ hai.", "zh", "vi"),
        ("下一周继续。", "Tiếp tục tuần sau.", "zh", "vi"),
        ("第二月继续。", "Tiếp tục tháng thứ hai.", "zh", "vi"),
        ("二分之一公斤", "0.5 kg", "zh", "vi"),
        ("四分之二公斤", "1/2 kg", "zh", "vi"),
        ("四分之三公斤", "0.75 kg", "zh", "vi"),
    ],
)
def test_equivalent_translation_must_pass(source, target, sl, tl):
    assert not check_translation_meaning(source, target, sl, tl, glossary={})


@pytest.mark.parametrize(
    "source,target,kind",
    [
        ("不准进去。", "Bạn được phép vào.", "negation_lost"),
        ("第二章", "Chương thứ ba", "ordinal_changed"),
        ("三分之一公斤", "1/2 kg", "quantity_lost"),
        ("二分之一公斤", "1/2 m", "unit_lost"),
        ("你知道吗", "Bạn đã biết.", "question_changed"),
        ("三公斤", "4 kg", "quantity_lost"),
    ],
)
def test_real_changes_still_rejected(source, target, kind):
    assert kind in {x["kind"] for x in check_translation_meaning(source, target, "zh", "vi", glossary={})}


@pytest.mark.parametrize(
    "source,target,sl,tl,kind",
    [
        ("第二天回来。", "Tháng thứ hai trở lại.", "zh", "vi", "unit_lost"),
        ("第二个月回来。", "Ngày thứ hai trở lại.", "zh", "vi", "unit_lost"),
        ("第三年回来。", "Năm thứ hai trở lại.", "zh", "vi", "ordinal_changed"),
        ("Next step.", "Bước hiện tại.", "en", "vi", "ordinal_changed"),
        ("First step.", "Bước thứ hai.", "en", "vi", "ordinal_changed"),
        ("第二章继续。", "Continue in the first chapter.", "zh", "en", "ordinal_changed"),
        ("第二步继续。", "Continue with 2 steps.", "zh", "en", "ordinal_changed"),
        ("二分之一公斤", "0.6 kg", "zh", "vi", "quantity_lost"),
        ("四分之三公斤", "0.75 m", "zh", "vi", "unit_lost"),
        ("零分之一公斤", "0 kg", "zh", "vi", "quantity_lost"),
        ("我的表不准。", "Đồng hồ của tôi chạy chuẩn.", "zh", "vi", "negation_lost"),
        ("温度不准。", "Nhiệt độ đo chính xác.", "zh", "vi", "negation_lost"),
        ("不准测温度。", "Được phép đo nhiệt độ.", "zh", "vi", "negation_lost"),
        ("Do not eat.", "Mange.", "en", "fr", "negation_lost"),
        ("Do not eat.", "Ne mange pas et ne bois pas.", "en", "fr", "negation_added"),
        ("Never eat it.", "Mange-le.", "en", "fr", "negation_lost"),
        ("这张表不准修改。", "Được sửa bảng sai.", "zh", "vi", "negation_lost"),
        ("第二天和第三个月回来。", "Tháng thứ hai và ngày thứ ba trở lại.", "zh", "vi", "ordinal_changed"),
        ("三周后回来。", "Trở lại sau 4 tuần.", "zh", "vi", "quantity_lost"),
    ],
)
def test_review_negative_controls(source, target, sl, tl, kind):
    assert kind in {x["kind"] for x in check_translation_meaning(source, target, sl, tl, glossary={})}


@pytest.mark.asyncio
@pytest.mark.parametrize("qc", ["standard", "strict"])
async def test_names_remain_consistent_when_later_batch_introduces_dictionary(monkeypatch, tmp_path, qc):
    class AI:
        provider = SimpleNamespace(value="test")
        model = "test"
        connection_name = "test"
        max_retries = 0
        calls = 0

        async def chat(self, **kw):
            self.calls += 1
            self.prompts.append(kw["messages"][0]["content"])
            if self.calls == 1:
                items = [{"id": f"s{i}", "text": "Trương Tam đã đến."} for i in range(12)]
            elif self.calls == 2:
                items = [{"id": "s12", "text": "Trương Ba đã đến.", "localized_terms": {"张三": "Trương Ba"}}]
            else:
                items = [{"id": f"s{i}", "text": "Trương Ba đã đến."} for i in range(12)]
            return json.dumps(items)

    ai = AI()
    ai.prompts = []
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *a: [ai])
    tx = LocalizationTranscript(
        language="zh",
        segments=[
            LocalizationSegment(
                id=f"s{i}", source_text="张三来了。", source_start_ms=i * 2000, source_end_ms=i * 2000 + 1000
            )
            for i in range(13)
        ],
    )
    args = dict(
        target_language="vi",
        profile=fast_localization_profile().model_copy(update={"qc_profile": qc}),
        user_settings=None,
        for_dub=True,
        checkpoint_dir=tmp_path,
    )
    if qc == "strict":
        with pytest.raises(RuntimeError, match="12 segment.*untranslated"):
            await svc.translate_transcript(tx, **args)
    else:
        out = await svc.translate_transcript(tx, **args)
        assert [seg.translation_status for seg in out.segments] == ["untranslated"] * 12 + ["success"]
        assert all(
            out.metadata["localized_terms"]["张三"] in seg.translated_text
            for seg in out.segments
            if seg.translation_status == "success"
        )
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["batches"]["0"]["items"] == []
    assert checkpoint["batches"]["0"]["pending_ids"] == [f"s{i}" for i in range(12)]
    resumed = await svc.translate_transcript(tx, **args)
    assert all(
        seg.translation_status == "success" and seg.translated_text == "Trương Ba đã đến." for seg in resumed.segments
    )
    assert ai.calls == 3
    assert '"张三": "Trương Ba"' in ai.prompts[-1]
    assert '"id": "s12"' not in ai.prompts[-1].split("SEGMENTS:\n", 1)[1]


@pytest.mark.asyncio
async def test_targeted_regeneration_keeps_grouping_after_accepted_rewrite(monkeypatch, tmp_path):
    calls = mock_dubbing(monkeypatch, durations=[1800, 800, 800, 800])
    tx = transcript(("Xin chào tất cả mọi người.", "Cảm ơn"), speakers=False)
    for i, seg in enumerate(tx.segments):
        seg.source_start_ms = i * 1100
        seg.source_end_ms = i * 1100 + 1000
        seg.overrides = {}

    async def rewrite(*a, **kw):
        return json.dumps([{"id": "s0", "text": "Xin chào"}])

    monkeypatch.setattr(svc, "_call_ai_chat_resilient", rewrite)
    args = dict(
        source_media=tmp_path / "s.mp4",
        target_language="Vietnamese",
        profile=fast_localization_profile(),
        user_settings=None,
        work_dir=tmp_path / "dub",
        reuse_existing_chunks=True,
    )
    first = await svc.synthesize_timed_dub(tx, **args)
    assert len(first.chunks) == 2 and first.rewrite_count == 1
    result = await svc.synthesize_timed_dub(tx, regenerate_chunk_ids={2}, **args)
    assert [c.item_indexes for c in result.chunks] == [c.item_indexes for c in first.chunks]
    assert [c[1] for c in calls if c[0] == "tts"] == ["Xin chào tất cả mọi người.", "Cảm ơn", "Xin chào", "Cảm ơn"]
    resumed = await svc.synthesize_timed_dub(tx, **args)
    assert [c.item_indexes for c in resumed.chunks] == [c.item_indexes for c in first.chunks]
    assert len([c for c in calls if c[0] == "tts"]) == 4
    assert resumed.rewrite_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("speaker", ["SPEAKER_00", " SPEAKER_00 ", "SPEAKER_01", ""])
async def test_speaker_text_save_preserves_hints_only_for_unchanged_owner(monkeypatch, tmp_path, speaker):
    monkeypatch.setattr(type(settings), "STORAGE_ROOT", property(lambda _: tmp_path))
    tx = source_voices(transcript(), ["male", "female"])
    run = LocalizationRun(
        "j", source_path=str(tmp_path / "s.mp4"), effective_profile=fast_localization_profile().model_dump()
    )
    p = run.save_json("translation", "transcript.json", tx.model_dump())
    run.complete("translation", artifacts={"transcript": p})
    state_path = run.root / "dubbing_state.json"
    state_path.write_text(json.dumps({"speaker_genders": tx.metadata["alignment"]["speaker_genders"]}))

    async def claim(*a, **kw):
        return {}

    async def restore(*a, **kw):
        pass

    monkeypatch.setattr(api, "_claim_job_mutation", claim)
    monkeypatch.setattr(api, "_restore_job_mutation", restore)
    monkeypatch.setattr(api, "_load_run", lambda _: run)
    monkeypatch.setattr(api, "_verify_run_source_identity", lambda _: None)
    rev = api._review_revision(run, "translation", "transcript")
    await api.localization_expert_override(
        "j",
        api.ExpertOverrideRequest(
            revision=rev, segments=[api.SegmentOverride(id="s0", translated_text="Chào bạn", speaker_id=speaker)]
        ),
    )
    saved = run.load_json("translation", "transcript")
    if speaker.strip() == "SPEAKER_00":
        assert saved["metadata"]["alignment"]["speaker_genders"] == tx.metadata["alignment"]["speaker_genders"]
        assert json.loads(state_path.read_text())["speaker_genders"] == tx.metadata["alignment"]["speaker_genders"]
    else:
        assert "speaker_genders" not in saved["metadata"]["alignment"]
        assert json.loads(state_path.read_text())["speaker_genders"] == {}
    assert saved["segments"][0]["speaker_id"] == (speaker.strip() or None)
