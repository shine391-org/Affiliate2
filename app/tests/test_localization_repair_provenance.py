"""Cue-local semantic repair receipts; real guards/checkpoints, MOCK AI only."""
import json

import pytest

from backend.core.localization import LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.localization_omissions import validate_translation_omissions
from tests.test_localization_auto_translation import MockAI, _auto, _ids, _install, _mixed_transcript, _run
from tests.test_localization_auto_omissions import omitted_tx


@pytest.mark.asyncio
async def test_last_rejected_repair_candidate_and_issues_survive_checkpoint(monkeypatch, tmp_path):
    initial = "Bản đầu bị từ chối"
    final = "Bản sửa cũng bị từ chối"

    def handler(prompt, call):
        return json.dumps([{"id": sid, "text": (initial if call == 1 else final) if sid == "bad" else "Câu đúng",
                           "localized_terms": ({"品牌": "thương hiệu"} if call == 1 else []) if sid == "bad" else {}}
                          for sid in _ids(prompt)], ensure_ascii=False)

    ai = MockAI(handler)
    profile = _auto()
    out = await _run(_mixed_transcript(), profile, monkeypatch, tmp_path, ai=ai)
    draft = out.metadata["translation_auto_drafts"]["bad"]
    assert out.segments[0].translated_text == final
    assert draft["target_text"] == final and draft["target_text"] != initial
    _, rejected, _ = svc._translation_candidates(
        json.dumps([{"id": "bad", "text": final, "localized_terms": []}]),
        [_mixed_transcript().segments[0]], {}, profile, "zh", "Vietnamese",
        context_segments=_mixed_transcript().segments)
    assert draft["issues"] == rejected["bad"]
    assert "localized_terms must be an object" in draft["issues"][0]["detail"]
    assert draft["reasons"] == [issue["kind"] for issue in rejected["bad"]]
    assert draft["semantic_attempts"] == 2 and draft["repair_attempted"] is True
    saved = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert saved["auto_drafts"]["bad"] == draft
    resumed = await _run(_mixed_transcript(), profile, monkeypatch, tmp_path, ai=ai)
    assert len(ai.prompts) == 2
    assert resumed.segments[0].translated_text == final
    assert resumed.metadata["translation_auto_drafts"]["bad"] == draft


@pytest.mark.asyncio
async def test_accepted_repair_still_succeeds_without_draft(monkeypatch, tmp_path):
    def handler(prompt, call):
        return json.dumps([{"id": sid, "text": "Thương hiệu rất tốt" if call > 1 else "Bản đầu bị từ chối",
                           "localized_terms": {"品牌": "Thương hiệu"} if sid == "bad" else {}}
                          for sid in _ids(prompt)], ensure_ascii=False)
    ai = MockAI(handler)
    out = await _run(_mixed_transcript(), _auto(), monkeypatch, tmp_path, ai=ai)
    assert out.segments[0].translation_status == "success"
    assert out.segments[0].translated_text == "Thương hiệu rất tốt"
    assert not out.metadata.get("translation_auto_drafts")


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_output", ["not JSON", "[]", '[{"id":"bad","text":""}]'])
async def test_invalid_repair_has_no_final_rejected_draft(monkeypatch, tmp_path, repair_output):
    def handler(prompt, call):
        if call > 1:
            return repair_output
        return json.dumps([{"id": sid, "text": "Lời 右台" if sid == "bad" else "Câu đúng"}
                          for sid in _ids(prompt)], ensure_ascii=False)
    out = await _run(_mixed_transcript(), _auto(qc_profile="standard"), monkeypatch,
                     tmp_path, ai=MockAI(handler))
    assert out.segments[0].translation_status == "untranslated"
    assert not out.metadata.get("translation_omissions")
    assert not out.metadata.get("translation_auto_drafts")


@pytest.mark.asyncio
async def test_route_dispatches_cannot_authorize_global_guard_only_omission(monkeypatch, tmp_path):
    tx = LocalizationTranscript(language="zh", segments=[LocalizationSegment(
        id=str(i), source_text="原文", source_start_ms=i*100, source_end_ms=(i+1)*100)
        for i in range(13)])
    original_guard = svc._translation_guard_checks

    def deferred_guard(batch, texts, *args, **kwargs):
        checks = original_guard(batch, texts, *args, **kwargs)
        # Model a late global invariant: no rejection/repair for cue 0 in its
        # initial batch, but final full-transcript checking demotes it.
        if len(batch) < len(tx.segments) and "0" in checks:
            checks["0"] = []
        return checks

    monkeypatch.setattr(svc, "_translation_guard_checks", deferred_guard)
    original_terms = svc._validate_localized_terms

    def deferred_terms(raw, batch, texts, *args, **kwargs):
        # The per-item dictionary guard also detects Han. Model the same late
        # evidence there; leave every other cue and the final guard unchanged.
        return original_terms(raw, batch,
            ["Lời dịch." if s.id == "0" else text for s, text in zip(batch, texts)],
            *args, **kwargs)

    monkeypatch.setattr(svc, "_validate_localized_terms", deferred_terms)
    ai = MockAI(lambda prompt, call: json.dumps([
        {"id": sid, "text": "Lời 右台" if sid == "0" else "Lời dịch."}
        for sid in _ids(prompt)], ensure_ascii=False))
    out = await _run(tx, _auto(), monkeypatch, tmp_path, ai=ai)
    assert len(ai.prompts) == 2, "unrelated second batch is not semantic repair"
    assert out.metadata["translation_ai"]["attempt_count"] == 2
    assert out.segments[0].translation_status == "draft"
    assert not out.metadata.get("translation_omissions")
    record = out.metadata["translation_auto_drafts"]["0"]
    assert record["origin"] == "final_global_guard"
    assert record.get("repair_attempted") is False
    assert record.get("semantic_attempts") == 1
    resumed = await _run(tx, _auto(), monkeypatch, tmp_path, ai=ai)
    assert resumed.segments[0].translation_status == "draft"
    assert not resumed.metadata.get("translation_omissions")
    assert len(ai.prompts) == 2


@pytest.mark.parametrize("defect", ["legacy", "no_repair", "wrong_id", "wrong_target", "wrong_speaker", "wrong_reasons"])
def test_omission_audit_requires_exact_cue_local_final_repair_receipt(defect):
    tx = omitted_tx()
    record = tx.metadata["translation_omissions"]["4"]
    record.update(segment_id="4", semantic_attempts=2, repair_attempted=True,
                  origin="semantic_repair", speaker_id=None, target_text=record["rejected_target_text"],
                  reasons=["unlocalized_text"])
    if defect == "legacy":
        for field in ("semantic_attempts", "repair_attempted", "origin", "segment_id"):
            record.pop(field)
        record["attempts"] = 999
    if defect == "no_repair": record["repair_attempted"] = False
    if defect == "wrong_id": record["segment_id"] = "other"
    if defect == "wrong_target": record["target_text"] = "older candidate"
    if defect == "wrong_speaker": record["speaker_id"] = "another speaker"
    if defect == "wrong_reasons": record["reasons"] = ["different_issue"]
    with pytest.raises(ValueError, match="omission audit"):
        validate_translation_omissions(tx, _auto())


@pytest.mark.asyncio
async def test_legacy_checkpoint_draft_never_gains_repair_provenance(monkeypatch, tmp_path):
    from tests.test_localization_auto_omissions import test_c6_omits_only_repaired_unreadable_candidate_and_resumes
    await test_c6_omits_only_repaired_unreadable_candidate_and_resumes(monkeypatch, tmp_path)
    cp_path = tmp_path / "checkpoint.json"
    cp = json.loads(cp_path.read_text(encoding="utf-8"))
    for field in ("semantic_attempts", "repair_attempted", "origin", "segment_id"):
        cp["auto_drafts"]["4"].pop(field, None)
    cp["auto_drafts"]["4"]["attempts"] = 999
    cp_path.write_text(json.dumps(cp, ensure_ascii=False), encoding="utf-8")
    tx = omitted_tx()
    tx.metadata = {}
    tx.segments[4].source_text = "下庭短的寶寶適合走一些右臺風"
    for s in tx.segments:
        s.translated_text = None; s.translation_status = "pending"
        s.target_start_ms = None; s.target_end_ms = None; s.tts_duration_ms = None
    ai = MockAI(lambda *_: pytest.fail("legacy draft must remain exact, not retranslate"))
    _install(monkeypatch, ai)
    out = await _run(tx, _auto(id="a", name="a"), monkeypatch, tmp_path, ai=ai)
    assert out.segments[4].translation_status == "draft"
    assert not out.metadata.get("translation_omissions")
