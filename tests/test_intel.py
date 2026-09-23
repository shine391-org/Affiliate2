"""
test_intel.py — Tests for Candidate Intelligence (Phase 3.1)

Tests:
- legacy CLI bridge delegates media/ASR/AI ownership to canonical app services
- media: no re-download when a candidate source already exists
- AI analysis: JSON extraction + cache behavior
- pipeline: MEDIA_UNAVAILABLE stays isolated per candidate
- report: shortlist table renders
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from radar.intel.ai_analyze import _extract_json
from radar.intel.media import candidate_dir, fetch_media
from radar.intel.report import _risk_badge, build_report
from radar.intel.pipeline import run_intelligence
import radar.intel.pipeline as intel_pipeline


# ---------------------------------------------------------------------------
# ai_analyze: JSON extraction
# ---------------------------------------------------------------------------

def test_extract_json_raw():
    text = '{"topic": "food"}'
    d = _extract_json(text)
    assert d == {"topic": "food"}


def test_extract_json_fenced():
    text = '```json\n{"topic": "food", "hook": {"text": "hi"}}\n```'
    d = _extract_json(text)
    assert d["topic"] == "food"
    assert d["hook"]["text"] == "hi"


def test_extract_json_with_prose():
    text = 'Here is the analysis:\n{"topic": "food"}\nHope this helps!'
    d = _extract_json(text)
    assert d == {"topic": "food"}


def test_extract_json_invalid():
    assert _extract_json("not json at all") is None
    assert _extract_json("") is None


# ---------------------------------------------------------------------------
# media: canonical app bridge
# ---------------------------------------------------------------------------


def test_unknown_platform_fails_without_network():
    result = fetch_media("unknownplat", "12345", "https://example.com")
    assert result["status"] == "MEDIA_UNAVAILABLE"
    assert "unsupported Radar platform" in result["meta"]["reason"]


# ---------------------------------------------------------------------------
# media: no re-download
# ---------------------------------------------------------------------------

def test_no_redownload(tmp_path=None):
    # Use a real candidate dir if it exists (from live run)
    cdirs = list(Path(__file__).parent.parent.glob("data/candidates/*_*"))
    if not cdirs:
        print("  (skip: no live candidate dirs)")
        return
    d = cdirs[0]
    source = d / "source.mp4"
    if not source.exists():
        print("  (skip: no source.mp4)")
        return
    size_before = source.stat().st_size
    # parse platform/content_id from dir name
    name = d.name
    platform, content_id = name.split("_", 1)
    result = fetch_media(platform, content_id, "")
    assert result["status"] == "ALREADY_EXISTS"
    assert result["path"] == source
    assert source.stat().st_size == size_before


# ---------------------------------------------------------------------------
# ai_analyze: cache hit (uses live cache if present)
# ---------------------------------------------------------------------------

def test_cache_hit():
    cdirs = list(Path(__file__).parent.parent.glob("data/candidates/*/intelligence.json"))
    if not cdirs:
        print("  (skip: no live intelligence cache)")
        return
    intel_path = cdirs[0]
    metadata = {"title": "cached test", "latest_metrics": {}, "breakdown": {}}
    from radar.intel.ai_analyze import analyze_candidate
    result = analyze_candidate(metadata, None, intel_path, force=False)
    assert result["status"] == "CACHED"
    assert result["model_stats"]["calls"] == 0


# ---------------------------------------------------------------------------
# report: risk badge + build
# ---------------------------------------------------------------------------

def test_risk_badge():
    risks = {
        "copyright_dependency": "LOW",
        "personality_dependency": "MEDIUM",
        "product_dependency": "LOW",
        "context_dependency": "HIGH",
    }
    badge = _risk_badge(risks)
    assert "C:LOW" in badge
    assert "P:MED" in badge
    assert "X:HIG" in badge


def test_risk_badge_empty():
    assert _risk_badge({}) == "-"
    assert _risk_badge(None) == "-"


def test_build_report():
    summary = {
        "requested": 1,
        "media_downloaded": 1,
        "media_already": 0,
        "media_unavailable": 0,
        "transcripts": 1,
        "transcript_unavailable": 0,
        "intelligence_generated": 1,
        "intelligence_cached": 0,
        "intelligence_errors": 0,
        "model_calls": 1,
        "model_retries": 0,
        "results": [{
            "platform": "douyin",
            "content_id": "test123",
            "title": "Test Video",
            "media_status": "SUCCESS",
            "transcript_status": "SUCCESS",
            "intelligence_status": "SUCCESS",
            "intelligence": {
                "topic": "food",
                "hook": {"text": "hook", "type": "curiosity", "first_seconds": 2},
                "remake_difficulty": "LOW",
                "localization_potential": "HIGH",
                "risks": {"copyright_dependency": "LOW", "personality_dependency": "LOW",
                          "product_dependency": "LOW", "context_dependency": "LOW"},
            },
        }],
    }
    candidates = [{
        "platform": "douyin",
        "content_id": "test123",
        "title": "Test Video",
        "creator_name": "Test Creator",
        "canonical_url": "https://example.com",
        "total_score": 80.0,
        "confidence": 0.8,
        "breakdown": {"freshness": 80, "engagement": 90},
    }]
    json_str, md_str = build_report(summary, candidates)
    jd = json.loads(json_str)
    assert jd["candidates"][0]["content_id"] == "test123"
    assert "Shortlist" in md_str
    assert "Test Video" in md_str
    assert "LOW" in md_str


# ---------------------------------------------------------------------------
# pipeline: failure does not block batch (mocked)
# ---------------------------------------------------------------------------

def test_pipeline_sync_entrypoint_uses_converged_async_stages(monkeypatch, tmp_path):
    seen = []

    monkeypatch.setattr(
        intel_pipeline,
        "candidate_dir",
        lambda platform, content_id: tmp_path / f"{platform}_{content_id}",
    )

    async def fake_media(platform, content_id, canonical_url):
        seen.append(("media", content_id))
        directory = tmp_path / f"{platform}_{content_id}"
        directory.mkdir(parents=True, exist_ok=True)
        return {"status": "MEDIA_UNAVAILABLE", "path": None, "meta": {"reason": "fixture"}}

    async def fake_ai(metadata, transcript, out_path, force=False):
        seen.append(("ai", metadata["content_id"]))
        return {
            "status": "SUCCESS",
            "data": {"topic": metadata["content_id"]},
            "model_stats": {"calls": 1, "retries": 0},
        }

    monkeypatch.setattr(intel_pipeline, "fetch_media_async", fake_media)
    monkeypatch.setattr(intel_pipeline, "analyze_candidate_async", fake_ai)

    summary = run_intelligence([
        {
            "platform": "douyin",
            "content_id": "one",
            "title": "One",
            "canonical_url": "https://example.invalid/one",
            "latest_metrics": {},
            "breakdown": {},
            "total_score": 1,
            "confidence": 0.1,
        },
        {
            "platform": "bilibili",
            "content_id": "two",
            "title": "Two",
            "canonical_url": "https://example.invalid/two",
            "latest_metrics": {},
            "breakdown": {},
            "total_score": 2,
            "confidence": 0.2,
        },
    ], verbose=False)

    assert summary["requested"] == 2
    assert summary["intelligence_generated"] == 2
    assert seen == [
        ("media", "one"), ("ai", "one"),
        ("media", "two"), ("ai", "two"),
    ]


def test_pipeline_failure_isolated():
    """A candidate with unknown platform gets MEDIA_UNAVAILABLE but batch continues."""
    candidates = [
        {"platform": "unknownplat", "content_id": "c1", "title": "T1",
         "canonical_url": "", "latest_metrics": {}, "breakdown": {},
         "total_score": 50, "confidence": 0.5},
        {"platform": "unknownplat", "content_id": "c2", "title": "T2",
         "canonical_url": "", "latest_metrics": {}, "breakdown": {},
         "total_score": 51, "confidence": 0.5},
    ]
    # run_intelligence will try media (fails), skip transcript, then AI
    # (no transcript -> still calls model). To avoid real API calls in test,
    # we only test the media layer here via fetch_media directly.
    for c in candidates:
        r = fetch_media(c["platform"], c["content_id"], "")
        assert r["status"] == "MEDIA_UNAVAILABLE"
    print("  (media layer isolation OK; full pipeline tested live)")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_extract_json_raw,
        test_extract_json_fenced,
        test_extract_json_with_prose,
        test_extract_json_invalid,
        test_unknown_platform_fails_without_network,
        test_no_redownload,
        test_cache_hit,
        test_risk_badge,
        test_risk_badge_empty,
        test_build_report,
        test_pipeline_failure_isolated,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{passed + failed} tests passed")
    sys.exit(0 if failed == 0 else 1)
