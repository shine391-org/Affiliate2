# SPDX-License-Identifier: AGPL-3.0-only
"""Focused URL, upload, batch and QC-delivery contract regressions."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import localization as api, tools
from backend.services import download_url_guard as guard


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://127.0.0.1:80/private",
    "https://10.1.2.3/movie",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:10.0.0.1]/",
    "http://192.0.2.1/",
    "http://[2001:db8::1]/",
    "http://localhost/video",
    "https://admin:secret@cdn.example.com/stream",
    "https://cdn.example.com:8080/video",
    "https://cdn.example.com\\@127.0.0.1/video",
    "http://singlelabel/video",
])
def test_download_guard_rejects_private_ambiguous_urls(url):
    with pytest.raises(ValueError):
        guard.validate_download_url(url)


def test_download_guard_allows_public_video_host_without_network():
    assert guard.validate_download_url(
        "https://www.douyin.com/video/123"
    ) == "https://www.douyin.com/video/123"


@pytest.mark.parametrize("address", [
    "224.0.0.1", "239.255.255.250", "ff02::1", "::ffff:224.0.0.1",
])
def test_download_guard_rejects_multicast_literals(address):
    host = f"[{address}]" if ":" in address else address
    with pytest.raises(ValueError):
        guard.validate_download_url(f"http://{host}/video")


@pytest.mark.parametrize("address", [
    "224.0.0.1", "239.255.255.250", "ff02::1", "::ffff:224.0.0.1",
])
def test_download_guard_rejects_mixed_public_multicast_dns(monkeypatch, address):
    monkeypatch.setattr(guard.socket, "getaddrinfo", lambda *_args, **_kw: [
        (None, None, None, None, ("8.8.8.8", 0)),
        (None, None, None, None, (address, 0)),
    ])
    with pytest.raises(ValueError):
        guard.validate_public_download_target("https://cdn.example.com/video")


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@creator/video/123",
    "https://[::ffff:8.8.8.8]/video",
])
def test_download_guard_keeps_public_targets(url):
    assert guard.validate_download_url(url) == url


def test_download_guard_dns_answer_must_all_be_global(monkeypatch):
    def answers_with_private(_host, _port, **_kwargs):
        return [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("10.0.0.7", 0)),
        ]
    monkeypatch.setattr(guard.socket, "getaddrinfo", answers_with_private)
    with pytest.raises(ValueError, match="private"):
        guard.validate_public_download_target("https://cdn.example.com/movie")

    monkeypatch.setattr(guard.socket, "getaddrinfo", lambda *_args, **_kw: [
        (None, None, None, None, ("8.8.8.8", 0)),
        (None, None, None, None, ("2606:4700:4700::1111", 0)),
    ])
    assert guard.validate_public_download_target("https://cdn.example.com/movie")


class ChunkedUpload:
    filename = "video.mp4"

    def __init__(self, data):
        self.data = data
        self.cursor = 0
        self.read_sizes = []

    async def read(self, size):
        assert size <= 1 << 20, "never allocate entire upload"
        self.read_sizes.append(size)
        part = self.data[self.cursor:self.cursor + size]
        self.cursor += len(part)
        return part


@pytest.mark.asyncio
async def test_tool_upload_streams_atomically_and_caps_midstream(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "tools_in_dir", lambda: tmp_path)
    ok = ChunkedUpload(b"x" * (2 * (1 << 20) + 23))
    target = await tools.save_upload(ok, "job1", {".mp4"}, 3 * (1 << 20))
    assert target.read_bytes() == ok.data
    assert len(ok.read_sizes) >= 3
    assert not list(tmp_path.glob("*.partial"))

    (tmp_path / "job2.mp4").write_bytes(b"previous")
    over = ChunkedUpload(b"z" * 18)
    with pytest.raises(HTTPException) as exc:
        await tools.save_upload(over, "job2", {".mp4"}, 12)
    assert exc.value.status_code == 413
    assert (tmp_path / "job2.mp4").read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.partial"))

    with pytest.raises(HTTPException) as exc:
        await tools.save_upload(ChunkedUpload(b""), "empty", {".mp4"}, 100)
    assert exc.value.status_code == 400
    assert not (tmp_path / "empty.mp4").exists()


@pytest.mark.asyncio
async def test_batch_continues_after_one_unexpected_runner_exception(monkeypatch, tmp_path):
    order = []
    failed = []
    async def attach(_job_id, _claim=None):
        return True
    async def run(**kwargs):
        order.append(kwargs["job_id"])
        if kwargs["job_id"] == "b":
            raise RuntimeError("unexpected runner defect")
    async def report(job_id, status, **kwargs):
        failed.append((job_id, status, kwargs.get("error_message")))
        return True

    monkeypatch.setattr(api, "_attach_batch_item", attach)
    monkeypatch.setattr("backend.core.tool_runners.run_tool_translate", run)
    monkeypatch.setattr("backend.agents.job_helper.update_job_status", report)
    await api._run_localization_batch_sequential([
        {"job_id": x, "in_path": tmp_path / f"{x}.mp4"} for x in "abc"
    ])
    assert order == ["a", "b", "c"]
    assert len(failed) == 1
    assert failed[0][0:2] == ("b", "failed")
    assert "unexpected runner defect" in failed[0][2]


@pytest.mark.asyncio
async def test_tool_success_preserves_qc_failed_job_completion_label(
    monkeypatch, tmp_path,
):
    from backend.core import tool_runners
    from backend.agents import job_helper
    from backend.core.ws_manager import ws_manager
    from backend.services import output_validator

    steps = []
    async def update(_job_id, status, **kwargs):
        steps.append((status, kwargs["current_step"], kwargs["output_data"]["qc_status"]))
        return True
    async def validate(_path, _expect):
        return SimpleNamespace(ok=True)
    async def send(*_args, **_kwargs):
        return None

    monkeypatch.setattr(job_helper, "update_job_status", update)
    monkeypatch.setattr(output_validator, "validate_output", validate)
    monkeypatch.setattr(ws_manager, "send", send)
    artifact = tmp_path / "video.mp4"
    artifact.write_bytes(b"artifact")
    ok = await tool_runners._tool_success(
        "job", artifact, [], "local", extra_output={"qc_status": "FAILED"},
        completion_step="Rendered · QC FAILED (not publishable)",
    )
    assert ok is True
    assert steps == [(
        "success", "Rendered · QC FAILED (not publishable)", "FAILED",
    )]


@pytest.mark.asyncio
async def test_agent_download_rejects_private_url_before_ai_correction(monkeypatch):
    from backend.core import task_runner
    from backend.services import ytdlp_service

    calls = []
    async def download_video(**_kwargs):
        calls.append("download")
        raise AssertionError("must reject before invoking downloader")

    monkeypatch.setattr(ytdlp_service, "download_video", download_video)
    with pytest.raises(ValueError):
        await task_runner._download_single_video_to_db(
            "job", "http://127.0.0.1/admin", "bad", "local",
        )
    assert calls == []


@pytest.mark.asyncio
async def test_batch_download_rejects_non_string_url_without_job():
    from backend.api import downloaded

    with pytest.raises(HTTPException) as exc:
        await downloaded.batch_download_from_urls({
            "urls": [{"url": 123}], "analyze": False,
        })
    assert exc.value.status_code == 400
