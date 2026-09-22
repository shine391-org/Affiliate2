"""
intel/media.py — Source video fetch for Candidate Intelligence (Phase 3.1)

Strategies:
  Douyin:   video_download_url from MediaCrawler JSONL (already captured at scan time)
  Bilibili: video page HTML -> __playinfo__ DASH streams -> download with resume -> ffmpeg merge

No re-download if source.mp4 already exists.
Marks MEDIA_UNAVAILABLE on failure without blocking the batch.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CANDIDATES_DIR = PROJECT_ROOT / "data" / "candidates"
MC_DOUYIN_JSONL = PROJECT_ROOT / "tools" / "MediaCrawler" / "data" / "douyin" / "jsonl"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def candidate_dir(platform: str, content_id: str) -> Path:
    d = CANDIDATES_DIR / f"{platform}_{content_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Douyin
# ---------------------------------------------------------------------------

def _load_douyin_jsonl_records() -> dict[str, dict]:
    """Load all Douyin JSONL records (any date) keyed by aweme_id."""
    records: dict[str, dict] = {}
    if not MC_DOUYIN_JSONL.exists():
        return records
    for f in sorted(MC_DOUYIN_JSONL.glob("search_contents_*.jsonl")):
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                aid = d.get("aweme_id")
                if aid and aid not in records:
                    records[aid] = d
            except json.JSONDecodeError:
                continue
    return records


def _download_simple(client: httpx.Client, url: str, out: Path, referer: str) -> bool:
    """Simple one-shot download (Douyin CDN gives complete files)."""
    try:
        r = client.get(url, headers={"Referer": referer}, timeout=120)
        if r.status_code == 200 and len(r.content) > 10000:
            out.write_bytes(r.content)
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Bilibili
# ---------------------------------------------------------------------------

_PLAYINFO_RE = re.compile(r"window\.__playinfo__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL)


def _fetch_playinfo(client: httpx.Client, aid: str) -> Optional[dict]:
    """Fetch video page and extract __playinfo__ DASH data."""
    url = f"https://www.bilibili.com/video/av{aid}"
    try:
        r = client.get(url, timeout=30)
        if r.status_code != 200:
            return None
        m = _PLAYINFO_RE.search(r.text)
        if not m:
            return None
        return json.loads(m.group(1))
    except Exception:
        return None


def _best_bandwidth_stream(streams: list[dict]) -> Optional[dict]:
    """Pick the highest-bandwidth DASH stream, not the smallest one."""
    candidates = [s for s in streams if isinstance(s, dict) and s.get("baseUrl")]
    if not candidates:
        return None
    return max(candidates, key=lambda s: int(s.get("bandwidth") or 0))


def _download_with_resume(
    client: httpx.Client, url: str, out: Path, max_retries: int = 5
) -> bool:
    """Download with Range resume for flaky CDN connections."""
    for attempt in range(max_retries):
        start = out.stat().st_size if out.exists() else 0
        try:
            headers = {"Referer": "https://www.bilibili.com/"}
            if start > 0:
                headers["Range"] = f"bytes={start}-"
            with client.stream("GET", url, headers=headers, timeout=90) as resp:
                if resp.status_code not in (200, 206):
                    time.sleep(2)
                    continue
                mode = "ab" if (resp.status_code == 206 and start > 0) else "wb"
                expected = int(resp.headers.get("content-length", 0))
                if mode == "ab":
                    expected += start
                with open(out, mode) as f:
                    for chunk in resp.iter_bytes(65536):
                        f.write(chunk)
                got = out.stat().st_size if out.exists() else 0
                if expected == 0 or got >= expected - 1000:
                    return True
        except Exception:
            if out.exists() and out.stat().st_size > 0:
                time.sleep(2)
                continue
            time.sleep(2)
    return False


def _merge_av(video_path: Path, audio_path: Path, out_path: Path) -> bool:
    """Merge DASH video+audio with ffmpeg -c copy."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(video_path), "-i", str(audio_path),
             "-c", "copy", str(out_path)],
            capture_output=True, text=True, timeout=300,
        )
        return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 10000
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_media(platform: str, content_id: str, canonical_url: str) -> dict:
    """
    Download source video for a candidate.

    Returns dict:
      status: SUCCESS | MEDIA_UNAVAILABLE | ALREADY_EXISTS
      path:   Path to source.mp4 (when available)
      meta:   download metadata
    """
    cdir = candidate_dir(platform, content_id)
    source = cdir / "source.mp4"

    if source.exists() and source.stat().st_size > 10000:
        return {"status": "ALREADY_EXISTS", "path": source,
                "meta": {"size": source.stat().st_size}}

    meta: dict = {"fetched_at": datetime.now(timezone.utc).isoformat()}
    ok = False

    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=120) as client:
            if platform == "douyin":
                records = _load_douyin_jsonl_records()
                rec = records.get(content_id)
                if not rec or not rec.get("video_download_url"):
                    meta["reason"] = "no video_download_url in MediaCrawler JSONL"
                else:
                    meta["url_source"] = "mediacrawler_jsonl"
                    ok = _download_simple(
                        client, rec["video_download_url"], source, "https://www.douyin.com/"
                    )
                    if ok:
                        meta["size"] = source.stat().st_size

            elif platform == "bilibili":
                # warmup homepage for cookies
                try:
                    client.get("https://www.bilibili.com/", timeout=15)
                except Exception:
                    pass
                pi = _fetch_playinfo(client, content_id)
                if not pi or not pi.get("data", {}).get("dash"):
                    meta["reason"] = "no playinfo/DASH in video page"
                else:
                    dash = pi["data"]["dash"]
                    v = _best_bandwidth_stream(dash.get("video", []))
                    a = _best_bandwidth_stream(dash.get("audio", []))
                    if not v or not a:
                        meta["reason"] = "empty DASH streams"
                    else:
                        meta["quality_id"] = v.get("id")
                        meta["video_bandwidth"] = v.get("bandwidth")
                        tmp_v = cdir / "_video.m4s"
                        tmp_a = cdir / "_audio.m4s"
                        ok_v = _download_with_resume(client, v["baseUrl"], tmp_v)
                        ok_a = _download_with_resume(client, a["baseUrl"], tmp_a)
                        if ok_v and ok_a:
                            ok = _merge_av(tmp_v, tmp_a, source)
                            meta["size"] = source.stat().st_size if ok else 0
                            # cleanup temps
                            for t in (tmp_v, tmp_a):
                                try:
                                    t.unlink()
                                except OSError:
                                    pass
                        else:
                            meta["reason"] = f"download failed (video={ok_v}, audio={ok_a})"
            else:
                meta["reason"] = f"unknown platform: {platform}"
    except Exception as exc:
        meta["reason"] = f"{type(exc).__name__}: {exc}"

    if not ok:
        # cleanup partial file
        if source.exists():
            try:
                source.unlink()
            except OSError:
                pass
        return {"status": "MEDIA_UNAVAILABLE", "path": None, "meta": meta}

    return {"status": "SUCCESS", "path": source, "meta": meta}
