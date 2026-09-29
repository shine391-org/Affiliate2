# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import candidate_intelligence_service as cis


def test_extract_candidate_json_accepts_fenced_and_rejects_non_object():
    fence = chr(96) * 3
    assert cis.extract_candidate_json(fence + 'json\n{"topic":"food"}\n' + fence) == {"topic": "food"}
    assert cis.extract_candidate_json("[1,2,3]") is None


@pytest.mark.asyncio
async def test_candidate_media_uses_canonical_downloader(monkeypatch, tmp_path):
    source = tmp_path / "source.mkv"
    seen = {}

    async def fake_download(**kwargs):
        seen.update(kwargs)
        source.write_bytes(b"x" * 20_000)
        return {
            "video_path": source,
            "duration": 12,
            "height": 1080,
            "media_metadata": {"container": "matroska"},
            "requested_quality": "best",
        }

    monkeypatch.setattr("backend.services.ytdlp_service.download_video", fake_download)
    result = await cis.fetch_candidate_media(
        "bilibili", "123", "https://www.bilibili.com/video/BVtest", tmp_path,
        title_hint="candidate caption",
    )
    assert result["status"] == "SUCCESS"
    assert result["path"] == source
    assert seen["extract_audio"] is False
    assert seen["options"]["quality"] == "best"
    assert seen["title_hint"] == "candidate caption"


@pytest.mark.asyncio
async def test_candidate_media_reuses_existing_source_without_download(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 20_000)

    async def should_not_download(**kwargs):
        raise AssertionError("existing source must be reused")

    monkeypatch.setattr("backend.services.ytdlp_service.download_video", should_not_download)
    monkeypatch.setattr(
        "backend.services.video_utils.probe_media_details",
        lambda path: {"duration_seconds": 1.0},
    )
    result = await cis.fetch_candidate_media(
        "douyin", "456", "https://www.douyin.com/video/456", tmp_path
    )
    assert result["status"] == "ALREADY_EXISTS"
    assert result["path"] == source


@pytest.mark.asyncio
async def test_candidate_transcribe_routes_to_canonical_whisper(monkeypatch, tmp_path):
    seen = {}

    async def fake_transcribe(path, **kwargs):
        seen["path"] = Path(path)
        seen.update(kwargs)
        return {
            "text": "hello world",
            "language": "en",
            "language_probability": 0.98,
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world", "words": []}],
        }

    monkeypatch.setattr(
        "backend.services.whisper_service.whisper_service.transcribe",
        fake_transcribe,
    )
    monkeypatch.setattr(
        "backend.services.video_utils.probe_duration",
        lambda *a, **k: 1.0,
    )
    monkeypatch.setattr(
        "backend.services.whisper_service.resolve_whisper_model",
        lambda quality, model_name=None: model_name or "small",
    )
    media = tmp_path / "source.mp4"
    media.write_bytes(b"media")
    out = tmp_path / "transcript.json"

    result = await cis.transcribe_candidate(media, out, quality="balanced")
    assert result["status"] == "SUCCESS"
    assert result["data"]["full_transcript"] == "hello world"
    assert result["data"]["detected_language"] == "en"
    assert seen["quality"] == "balanced"
    assert json.loads(out.read_text(encoding="utf-8"))["segments"][0]["end"] == 1.0


@pytest.mark.asyncio
async def test_candidate_analysis_uses_canonical_ai_and_radar_metadata(monkeypatch, tmp_path):
    prompts = []

    class FakeAI:
        provider = SimpleNamespace(value="compatible")
        model = "local-test"

        async def chat(self, messages, system=None, max_tokens=0):
            prompts.append((messages[0]["content"], system, max_tokens))
            return '{"topic":"food","remake_difficulty":"LOW","localization_potential":"HIGH"}'

    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", lambda settings=None: FakeAI())
    out = tmp_path / "intelligence.json"
    result = await cis.analyze_candidate_intelligence(
        {
            "title": "demo",
            "latest_metrics": {"views": 10},
            "radar_total_score": 88.5,
            "radar_confidence": 0.9,
            "radar_breakdown": {"engagement": 90},
        },
        {"detected_language": "zh", "full_transcript": "你好"},
        out,
    )
    assert result["status"] == "SUCCESS"
    assert "total=88.5" in prompts[0][0]
    assert "engagement" in prompts[0][0]
    assert prompts[0][2] == cis.MAX_TOKENS
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["_meta"]["pipeline"] == "canonical_candidate_intelligence_v1"


@pytest.mark.asyncio
async def test_candidate_analysis_cache_avoids_provider(monkeypatch, tmp_path):
    out = tmp_path / "intelligence.json"
    out.write_text('{"topic":"cached"}', encoding="utf-8")

    def should_not_get_ai(*args, **kwargs):
        raise AssertionError("cache hit must not resolve AI provider")

    monkeypatch.setattr("backend.core.ai_provider.get_ai_client", should_not_get_ai)
    result = await cis.analyze_candidate_intelligence({}, None, out)
    assert result["status"] == "CACHED"
    assert result["model_stats"]["calls"] == 0



@pytest.mark.asyncio
async def test_radar_pipeline_passes_candidate_title_to_media_fetch(monkeypatch, tmp_path):
    from src.radar.intel import pipeline

    seen = {}

    monkeypatch.setattr(
        pipeline,
        "candidate_dir",
        lambda platform, content_id: tmp_path / f"{platform}_{content_id}",
    )

    async def fake_media(platform, content_id, canonical_url, *, title_hint=""):
        seen.update({
            "platform": platform,
            "content_id": content_id,
            "canonical_url": canonical_url,
            "title_hint": title_hint,
        })
        return {
            "status": "MEDIA_UNAVAILABLE",
            "path": None,
            "meta": {"reason": "test"},
        }

    async def fake_analyze(metadata, transcript, out_path, force=False):
        return {
            "status": "ERROR",
            "data": None,
            "model_stats": {"calls": 0, "retries": 0},
        }

    monkeypatch.setattr(pipeline, "fetch_media_async", fake_media)
    monkeypatch.setattr(pipeline, "analyze_candidate_async", fake_analyze)

    result = await pipeline.run_intelligence_async(
        [{
            "platform": "douyin",
            "content_id": "123",
            "title": "这个产品很好用",
            "canonical_url": "https://www.douyin.com/video/123",
        }],
        verbose=False,
    )

    assert result["requested"] == 1
    assert seen["title_hint"] == "这个产品很好用"
    assert seen["canonical_url"].endswith("/video/123")
