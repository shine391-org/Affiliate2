"""Phase F3: one Planner contract over native video, metrics, jobs and Publish."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from backend.agents.planner import PlannerAgent, _followup_sink
from backend.database import AsyncSessionLocal, init_db
from backend.models.generated_video import GeneratedVideo
from backend.models.job import Job
from backend.models.video_metrics import VideoMetrics
from backend.models.user_settings import UserSettings
from backend.services.performance_tracker import _save_metrics

UID = "test-phase-f-messaging"


@pytest.fixture(autouse=True)
def clean_user():
    async def clean():
        await init_db()
        async with AsyncSessionLocal() as db:
            ids = (await db.execute(select(GeneratedVideo.id).where(GeneratedVideo.user_id == UID))).scalars().all()
            if ids:
                await db.execute(delete(VideoMetrics).where(VideoMetrics.generated_video_id.in_(ids)))
            await db.execute(delete(GeneratedVideo).where(GeneratedVideo.user_id == UID))
            await db.execute(delete(Job).where(Job.user_id == UID))
            await db.execute(delete(UserSettings).where(UserSettings.user_id == UID))
            await db.commit()
    asyncio.run(clean())
    yield
    asyncio.run(clean())


async def asset(title, *, approved=False, published=False, platform="youtube"):
    from backend.agents.uploader import file_sha256
    from backend.config import settings

    settings.TMP_DIR.mkdir(parents=True, exist_ok=True)
    media = settings.TMP_DIR / "phase-f-messaging-test.mp4"
    media.write_bytes(b"phase-f-messaging-fixture")
    artifact_hash = file_sha256(media) if approved else None
    row = GeneratedVideo(
        user_id=UID,
        title=title,
        status="uploaded" if published else "ready",
        video_path=str(media),
        artifact_sha256=artifact_hash,
        publish_approved_at=datetime.utcnow() if approved else None,
        publish_approval_artifact_sha256=artifact_hash,
        uploaded_platforms_json=f'["{platform}"]' if published else "[]",
        youtube_video_id=f"yt-{title}" if published and platform == "youtube" else None,
    )
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()
        return row.id


async def action_reply(action, monkeypatch):
    from backend.core.ws_manager import ws_manager
    async def send(*args, **kwargs):
        return None
    monkeypatch.setattr(ws_manager, "send", send)
    monkeypatch.setattr(ws_manager, "send_constraint_warning", send)
    planner = PlannerAgent()
    replies = []
    token = _followup_sink.set(replies)
    try:
        await planner._dispatch_action(action, None, UID)
    finally:
        _followup_sink.reset(token)
    return "\n".join(replies)


def test_planner_rejects_non_object_and_unknown_actions_without_side_effects(monkeypatch):
    async def go():
        malformed = await action_reply([], monkeypatch)
        assert "không hợp lệ" in malformed.lower()
        unknown = await action_reply({"type": "click_arbitrary_ui"}, monkeypatch)
        assert "không có action backend" in unknown.lower()
        assert "không có thao tác nào được thực hiện" in unknown.lower()
    asyncio.run(go())


def test_generated_asset_queries_are_exact_and_missing_is_explicit(monkeypatch):
    async def go():
        a = await asset("Alpha")
        b = await asset("Beta", approved=True)
        listing = await action_reply({"type": "show_videos"}, monkeypatch)
        assert a in listing and b in listing
        assert "Alpha" in listing and "Beta" in listing
        exact = await action_reply({"type": "show_video", "generated_video_id": a}, monkeypatch)
        assert a in exact and "Alpha" in exact and "Beta" not in exact
        assert "approval" in exact.lower()
        status = await action_reply({"type": "show_publish_status", "generated_video_id": b}, monkeypatch)
        assert "approved" in status.lower()
        missing = await action_reply({"type": "show_video", "generated_video_id": "missing"}, monkeypatch)
        assert "not found" in missing.lower()
        malformed = await action_reply({"type": "show_video", "generated_video_id": [a]}, monkeypatch)
        assert "valid" in malformed.lower()
    asyncio.run(go())


def test_performance_queries_use_native_identity_null_and_stale(monkeypatch):
    async def go():
        a = await asset("Alpha", published=True)
        b = await asset("Beta", published=True)
        await _save_metrics(a, "youtube", {"views": 140, "likes": None, "comments": None})
        from sqlalchemy import update
        async with AsyncSessionLocal() as db:
            await db.execute(update(VideoMetrics).where(VideoMetrics.generated_video_id == a)
                .values(fetched_at=datetime.utcnow() - timedelta(days=3)))
            await db.commit()
        exact = await action_reply({"type": "show_performance", "generated_video_id": a}, monkeypatch)
        assert a in exact and "140" in exact and "Beta" not in exact
        assert "unavailable" in exact.lower() and "last sync" in exact.lower()
        unknown = await action_reply({"type": "show_performance", "generated_video_id": b}, monkeypatch)
        assert "insufficient" in unknown.lower() and "140" not in unknown
        board = await action_reply({"type": "show_youtube_performance"}, monkeypatch)
        assert a in board and "140" in board
        recent = await action_reply({"type": "show_outperforming_videos"}, monkeypatch)
        assert "140" in recent and "not a proven" in recent.lower()
        missing = await action_reply({"type": "show_performance", "generated_video_id": "missing"}, monkeypatch)
        assert "not found" in missing.lower()
    asyncio.run(go())


def test_job_status_includes_backend_error_and_missing_is_explicit(monkeypatch):
    async def go():
        async with AsyncSessionLocal() as db:
            job = Job(user_id=UID, job_type="upload", status="failed", error_message="OAuth expired")
            db.add(job)
            await db.commit()
            jid = job.id
        failure = await action_reply({"type": "show_job_status", "job_id": jid}, monkeypatch)
        assert "OAuth expired" in failure and "failed" in failure
        missing = await action_reply({"type": "show_job_status", "job_id": "missing"}, monkeypatch)
        assert "not found" in missing.lower()
    asyncio.run(go())


def test_planner_text_uses_backend_fact_instead_of_invented_llm_metric(monkeypatch):
    async def go():
        a = await asset("Alpha", published=True)
        planner = PlannerAgent()
        async def context(*args):
            return dict(ctx={}, suggestions=[], cred_status={}, user_profile=None,
                failures_text="", perf_insights=None, news_ctx=None, pipeline={})
        class AI:
            async def chat(self, **kwargs):
                return ("Alpha got 999999 views! "
                    f'<action>{{"type":"show_performance","generated_video_id":"{a}"}}</action>')
        monkeypatch.setattr(planner, "_gather_common_context", context)
        monkeypatch.setattr("backend.agents.planner.get_ai_client", lambda _: AI())
        async def record(*args, **kwargs):
            pass
        monkeypatch.setattr(planner.intelligence, "record_event", record)
        from backend.core.ws_manager import ws_manager
        async def send(*args, **kwargs):
            pass
        monkeypatch.setattr(ws_manager, "send", send)
        text = await planner.handle_message_text("show performance", None, UID)
        assert "999999" not in text and "insufficient" in text.lower()
    asyncio.run(go())


def test_publish_uses_canonical_queue_once_and_reports_job(monkeypatch):
    async def go():
        vid = await asset("Approved", approved=True)
        async with AsyncSessionLocal() as db:
            db.add(UserSettings(user_id=UID, youtube_credentials_json_encrypted="stored-token"))
            await db.commit()
        dispatched = []
        monkeypatch.setattr("backend.core.task_runner.run_upload", lambda **kwargs: kwargs)
        monkeypatch.setattr("backend.core.task_runner.dispatch", dispatched.append)
        reply = await action_reply({"type": "start_upload", "generated_video_id": vid,
                                    "platforms": ["youtube"]}, monkeypatch)
        assert "job" in reply.lower() and vid in reply
        assert len(dispatched) == 1 and dispatched[0]["generated_video_id"] == vid
        retry = await action_reply({"type": "start_upload", "generated_video_id": vid,
                                    "platforms": ["youtube"]}, monkeypatch)
        assert "already" in retry.lower() or "pending" in retry.lower()
        assert len(dispatched) == 1
        async with AsyncSessionLocal() as db:
            jobs = (await db.execute(select(Job).where(Job.user_id == UID))).scalars().all()
            assert len(jobs) == 1 and jobs[0].id in reply
    asyncio.run(go())


@pytest.mark.parametrize("case", ["unapproved", "missing", "malformed", "disconnected", "duplicate", "invalid_destination"])
def test_publish_rejection_never_creates_orphan_job(monkeypatch, case):
    async def go():
        vid = await asset("Target", approved=case != "unapproved",
                          published=case == "duplicate")
        async with AsyncSessionLocal() as db:
            if case != "disconnected":
                db.add(UserSettings(user_id=UID, youtube_credentials_json_encrypted="stored-token"))
                await db.commit()
        selected = [vid] if case == "malformed" else "missing" if case == "missing" else vid
        platforms = ["myspace"] if case == "invalid_destination" else ["youtube"]
        dispatched = []
        monkeypatch.setattr("backend.core.task_runner.run_upload", lambda **kwargs: kwargs)
        monkeypatch.setattr("backend.core.task_runner.dispatch", dispatched.append)
        reply = await action_reply({"type": "start_upload", "generated_video_id": selected,
                                    "platforms": platforms}, monkeypatch)
        assert reply
        async with AsyncSessionLocal() as db:
            assert not (await db.execute(select(Job).where(Job.user_id == UID))).scalars().all()
        assert not dispatched
    asyncio.run(go())


def test_review_publish_metrics_messaging_preserve_exact_asset(monkeypatch, tmp_path):
    async def go():
        from backend.api.videos import PublishApprovalRequest, set_publish_approval
        from backend.agents.uploader import UploadAgent
        a, b = await asset("Reviewed A"), await asset("Other B")
        media = tmp_path / "reviewed.mp4"
        media.write_bytes(b"reviewed fixture")
        async with AsyncSessionLocal() as db:
            row = await db.get(GeneratedVideo, a)
            row.video_path = str(media)
            db.add(UserSettings(user_id=UID, youtube_credentials_json_encrypted="stored-test-token"))
            await db.commit()
            approved = await set_publish_approval(a, PublishApprovalRequest(approved=True), db=db)
            assert approved["id"] == a and approved["publish_approved"]
            assert (await db.get(GeneratedVideo, b)).publish_approved_at is None
        submitted = []
        monkeypatch.setattr("backend.core.task_runner.run_upload", lambda **kwargs: kwargs)
        monkeypatch.setattr("backend.core.task_runner.dispatch", submitted.append)
        response = await action_reply({"type": "start_upload", "generated_video_id": a,
                                       "platforms": ["youtube"]}, monkeypatch)
        assert a in response and len(submitted) == 1
        assert submitted[0]["generated_video_id"] == a
        assert "approved" in (await action_reply({"type": "start_upload",
                         "generated_video_id": b}, monkeypatch)).lower()
        async with AsyncSessionLocal() as db:
            jobs = (await db.execute(select(Job).where(Job.user_id == UID))).scalars().all()
            assert len(jobs) == 1 and jobs[0].id in response
            job_id = jobs[0].id
        async def fake_upload(self, video, settings):
            assert video.id == a
            return {"video_id": "real-shape-id-a", "url": "https://youtu.be/real-shape-id-a"}
        monkeypatch.setattr(UploadAgent, "_upload_youtube", fake_upload)
        from backend.core.ws_manager import ws_manager
        async def send(*args, **kwargs):
            pass
        monkeypatch.setattr(ws_manager, "send", send)
        monkeypatch.setattr(ws_manager, "send_progress", send)
        await UploadAgent().run(job_id, a, ["youtube"], UID)
        async with AsyncSessionLocal() as db:
            row_a = await db.get(GeneratedVideo, a)
            row_b = await db.get(GeneratedVideo, b)
            assert row_a.status == "uploaded" and row_a.youtube_video_id == "real-shape-id-a"
            assert row_b.status == "ready" and not row_b.youtube_video_id
            assert (await db.get(Job, job_id)).status == "success"
        await _save_metrics(a, "youtube", {"views": 91, "likes": None, "comments": None})
        answer_a = await action_reply({"type": "show_performance", "generated_video_id": a}, monkeypatch)
        answer_b = await action_reply({"type": "show_performance", "generated_video_id": b}, monkeypatch)
        assert a in answer_a and "views 91" in answer_a and b not in answer_a
        assert "insufficient data" in answer_b.lower() and "views 91" not in answer_b
    asyncio.run(go())


def test_provider_failure_remains_visible_as_exact_job_error(monkeypatch):
    async def go():
        from backend.agents.uploader import UploadAgent
        a = await asset("Provider failure", approved=True)
        async with AsyncSessionLocal() as db:
            db.add(UserSettings(user_id=UID, youtube_credentials_json_encrypted="stored-test-token"))
            await db.commit()
        submitted = []
        monkeypatch.setattr("backend.core.task_runner.run_upload", lambda **kwargs: kwargs)
        monkeypatch.setattr("backend.core.task_runner.dispatch", submitted.append)
        reply = await action_reply({"type": "start_upload", "generated_video_id": a}, monkeypatch)
        assert submitted and a in reply
        async with AsyncSessionLocal() as db:
            job_id = (await db.execute(select(Job).where(Job.user_id == UID))).scalar_one().id
        async def fail(self, video, settings):
            raise RuntimeError("provider upload rejected this content")
        monkeypatch.setattr(UploadAgent, "_upload_youtube", fail)
        from backend.core.ws_manager import ws_manager
        async def send(*args, **kwargs):
            pass
        monkeypatch.setattr(ws_manager, "send", send)
        monkeypatch.setattr(ws_manager, "send_progress", send)
        await UploadAgent().run(job_id, a, ["youtube"], UID)
        status = await action_reply({"type": "show_job_status", "job_id": job_id}, monkeypatch)
        assert "failed" in status and "provider upload rejected this content" in status
    asyncio.run(go())


def test_canonical_job_transitions_notify_channels_once_with_actual_error(monkeypatch):
    async def go():
        from backend.agents.job_helper import create_job, update_job_status
        from backend.messaging.base import NotificationEvent
        from backend.messaging.manager import messaging
        events = []
        async def notify(event, user_id, **data):
            events.append((event, user_id, data))
        monkeypatch.setattr(messaging, "notify", notify)
        ready = await create_job("generate", UID, {"generated_video_id": "asset-1"})
        failed = await create_job("upload", UID, {"generated_video_id": "asset-2"})
        await update_job_status(ready.id, "success", output_data={"generated_video_id": "asset-1"})
        await update_job_status(ready.id, "success", output_data={"generated_video_id": "asset-1"})
        await update_job_status(failed.id, "failed", error_message="OAuth token expired: reconnect in Settings")
        await asyncio.sleep(0.05)
        assert len(events) == 2
        assert events[0][0] == NotificationEvent.VIDEO_GENERATED
        assert events[0][2]["generated_video_id"] == "asset-1"
        assert events[1][0] == NotificationEvent.JOB_FAILED
        assert events[1][2]["error"] == "OAuth token expired: reconnect in Settings"
        assert all(event[1] == UID for event in events)
    asyncio.run(go())


def test_web_chat_never_streams_untrusted_metric_tokens(monkeypatch):
    async def go():
        a = await asset("Watched A", published=True)
        planner = PlannerAgent()
        async def context(*args):
            return dict(ctx={}, suggestions=[], cred_status={}, user_profile=None,
                        failures_text="", perf_insights=None, news_ctx=None, pipeline={})
        class AI:
            async def chat_stream(self, **kwargs):
                yield "Invented 888888 views! "
                yield f'<action>{{"type":"show_performance","generated_video_id":"{a}"}}</action>'
        monkeypatch.setattr(planner, "_gather_common_context", context)
        monkeypatch.setattr("backend.agents.planner.get_ai_client", lambda _: AI())
        from backend.core.ws_manager import ws_manager
        messages = []
        async def send(message, user_id):
            messages.append(message)
        monkeypatch.setattr(ws_manager, "send", send)
        await planner.handle_message("show performance", [], None, UID)
        assert not any(m["type"] == "chat_token" for m in messages)
        assert next(m for m in messages if m["type"] == "chat_done")["full_response"] == ""
        facts = [m.get("content", "") for m in messages if m["type"] == "assistant_message"]
        assert facts and "insufficient" in facts[-1].lower()
        assert "888888" not in " ".join(str(m) for m in messages)
    asyncio.run(go())



