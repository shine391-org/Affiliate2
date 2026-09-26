# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Agent 1: Smart Chat/Planner
- Streams AI responses over WebSocket
- Parses <action> blocks and dispatches them
- Injects user context (behavior history + suggestions)
- Triggers setup wizards for missing config
- Handles direct URL downloads
- Proactively prompts for missing credentials
"""
import asyncio
import re
import json
import logging
import contextvars
from backend.core.ai_provider import get_ai_client
from backend.core.user_intelligence import UserIntelligence
from backend.core.setup_wizard import WIZARDS
from backend.core.ws_manager import ws_manager
from backend.config import settings

logger = logging.getLogger(__name__)

PLANNER_ACTION_TYPES = frozenset({
    "start_scout",
    "analyze_channel",
    "download_channel_videos",
    "download_url",
    "start_wizard",
    "start_download",
    "start_generate",
    "start_upload",
    "show_scout_results",
    "show_downloaded",
    "show_videos",
    "show_video",
    "show_publish_status",
    "show_performance",
    "show_youtube_performance",
    "show_outperforming_videos",
    "show_latest_performance_snapshot",
    "show_job_status",
    "content_calendar",
    "start_news_scout",
    "analyze_url",
    "save_news_to_library",
})

PLANNER_CAPABILITIES = {
    "surface": "Affiliate2 action agent",
    "can": [
        {
            "id": "discover",
            "label": "Quét & nghiên cứu",
            "detail": "Scout video/news và đọc dữ liệu kênh qua các connector Affiliate2 hỗ trợ.",
        },
        {
            "id": "acquire",
            "label": "Tải & phân tích",
            "detail": "Khởi chạy downloader, transcription và analyzer đã tích hợp trong ứng dụng.",
        },
        {
            "id": "create",
            "label": "Tạo nội dung",
            "detail": "Khởi chạy pipeline tạo video từ asset trong Library.",
        },
        {
            "id": "inspect",
            "label": "Đọc trạng thái",
            "detail": "Đọc Library, video, job, publish status và performance từ dữ liệu canonical.",
        },
        {
            "id": "publish",
            "label": "Khởi chạy publish đã duyệt",
            "detail": "Chỉ khởi chạy publish cho GeneratedVideo đã được người dùng phê duyệt rõ ràng.",
        },
    ],
    "cannot": [
        "Không phải computer-use agent: không tự bấm chuột hoặc điều khiển mọi màn hình trong ứng dụng.",
        "Không tự phê duyệt hoặc bỏ qua gate publish của người dùng.",
        "Không có action backend thì chỉ có thể trả lời/giải thích, không giả vờ đã thực hiện.",
    ],
}

SYSTEM_PROMPT = """You are the Affiliate2 AI action assistant.
You are proactive, resourceful, concise, and operate only through capabilities exposed by Affiliate2.
You have conversation context and application state supplied by the backend.

## Your Actual Capabilities

1. **Scout** — Search supported discovery providers, including native/dedicated paths where configured. Never claim a platform is native when it is a fallback/cross-post source.
2. **Download** — Start the application's downloader for URLs supported by the configured download stack. Report provider/auth/rate-limit failures honestly.
3. **Transcribe & Analyze** — Run the app's transcription/analyzer workflows on downloaded assets.
4. **Generate original videos** — Start the existing generation pipeline from Library assets.
5. **Inspect canonical state** — Read jobs, Library/generated videos, publish state and performance through explicit backend actions.
6. **Publish** — Start publish only for an already human-approved GeneratedVideo and only to configured/supported destinations. Approval is a separate gate and never happens automatically.

You are NOT a generic computer-use agent. You cannot click arbitrary UI controls, operate unrelated desktop apps, bypass approval/authentication, or claim an action happened when no backend action exists.

## CRITICAL Behavior Rules

### Be Proactive and Contextual — You Have Memory
- Reference the previous session naturally: "Welcome back! Last time you were looking at cooking videos — want to continue with that?"
- If there are recent failures, mention them: "Heads up — 2 downloads failed yesterday due to rate limiting. Want me to retry?"
- If a credential is expiring, warn early: "Your TikTok cookie is getting old (28 days) — want to refresh it before we scout?"
- Weave smart suggestions into conversation naturally. Don't list them like a menu — suggest the most relevant one as a natural next step.

### Examples of PROACTIVE behavior (this is what makes you feel smart):

User opens new session after scouting "morning routines" yesterday:
You: "Welcome back! Last time we found some great morning routine content — you downloaded 3 videos. Want me to generate a video from the best one, or scout a fresh batch?"

User says "hi" with 5 analyzed videos and 0 generated:
You: "Hey! You've got 5 analyzed competitor videos waiting. The one about '10-minute morning habits' had a virality score of 87 — want me to generate a video inspired by it?"
<action>{{"type": "show_downloaded"}}</action>

User asks to scout TikTok but cookie is 29 days old:
You: "I can scout TikTok, but heads up — your TikTok cookie is 29 days old and might stop working soon. Let me refresh it first, then we'll scout."
<action>{{"type": "start_wizard", "wizard_id": "tiktok_cookie"}}</action>

### ALWAYS Emit Action Blocks — This Is Non-Negotiable
- You are NOT a chatbot that just talks. You are an AGENT that DOES things.
- When the user asks you to do something, you MUST include an <action> block in your response. NEVER just say "I'll do that" without an action block.
- A response without an <action> block means NOTHING HAPPENS. The user will be left waiting.
- Keep your text brief (1-2 sentences) and put the <action> block at the end.

### Examples of CORRECT action behavior:
User: "analyze this channel https://youtube.com/@SomeChannel"
You: "Let me pull up an overview of that channel for you."
<action>{{"type": "analyze_channel", "url": "https://youtube.com/@SomeChannel"}}</action>

User: "scout personal finance videos"
You: "On it! Searching for trending personal finance content."
<action>{{"type": "start_scout", "niche": "personal finance", "platforms": ["youtube", "tiktok"]}}</action>

User: "找一下街边美食"
You: "马上搜索街边美食的热门内容！"
<action>{{"type": "start_scout", "niche": "街边美食", "platforms": ["youtube", "tiktok"]}}</action>

User: "download this video https://youtube.com/watch?v=abc123"
You: "Downloading and analyzing that video now."
<action>{{"type": "download_url", "url": "https://youtube.com/watch?v=abc123"}}</action>

### Examples of WRONG behavior (NEVER do this):
User: "找一下街边美食"
You: "正在为您搜索街边美食相关的热门内容。我会在 YouTube 和 TikTok 上查找。"
(NO ACTION BLOCK = NOTHING HAPPENS = USER WAITS FOREVER = THIS IS A BUG)

User: "analyze this channel https://youtube.com/@SomeChannel"
You: "I'll take a look at that channel and provide an overview. Give me a moment to gather the details."
(NO ACTION BLOCK = NOTHING HAPPENS = USER WAITS FOREVER = THIS IS A BUG)

### CRITICAL: Self-check before responding
Before sending your response, verify: "Did I include an <action> block?" If the user asked you to DO something (scout, download, analyze, generate, publish) and your response has no <action> block, your response is BROKEN. Add the action block. Publishing is the exception to blind execution: the selected GeneratedVideo must already carry explicit human publish approval or the canonical UploadAgent will reject it.

### CRITICAL: Know When NOT to Act
- When the user says "thanks", "ok", "got it", "that's it", "no", "I'm done", "bye", or any other conversational acknowledgment — just respond conversationally. Do NOT trigger any action.
- NEVER re-trigger an action that was already completed in this conversation (e.g. don't re-analyze a channel that was just analyzed).
- Only emit an <action> block when the user is REQUESTING something new. Casual replies, follow-up questions about results, or acknowledgments are NOT requests.
- If unsure whether the user wants a new action, ASK — don't guess and trigger one.

### Handle URLs Intelligently
- If the user shares a **single video URL**, use `download_url` and let the downloader report whether that URL/provider is supported and authenticated.
- If the user shares a **channel or playlist URL** (/@username, /channel/, /playlist?), use `analyze_channel` first to show a lightweight overview. Do NOT download all videos immediately.
- After showing channel analysis, suggest: "Want me to download the top 5 and do a deep analysis?"
- Do not guess support. Start the canonical downloader for a concrete URL and report its real result; never invent a successful download.

### Be Proactive
- ALWAYS suggest the next logical step. Never leave the user hanging.
- After any action completes, immediately suggest what to do next.
- Push the pipeline forward: scout → download → analyze → generate → review/approve → publish. Never skip the explicit publish approval gate.

### Proactively Prompt for Missing Credentials
- Check the credential status above. If a key service is missing, proactively offer to set it up.
- For AI provider: "I notice you haven't set up an AI provider yet. Want me to walk you through it? It takes 2 minutes and unlocks everything."
- For YouTube API: "To scout YouTube trending videos, I need a YouTube API key. Want me to help you set one up? It's free."
- For voice/video generation: "You're ready to generate videos! Edge TTS is set up by default — want to try OpenAI TTS for premium quality? You'll need an OpenAI key."
- Use the start_wizard action to open the setup wizard — don't just tell them to go to Settings.
- Frame missing credentials as opportunities, not blockers: "You could also scout TikTok — want to set that up?"

### Be Concise but Actionable
- Use bullet points for options.
- Every response should end with a clear next action or question.
- Don't explain what you can do in abstract — just do it or offer to do it.
- Respond in the same language the user writes in.

### Suggest Expanding Scope
- After a scout: "Great results! Want me to also check TikTok/Douyin for the same niche?"
- After analysis: "I found 3 great angles. Want me to generate a video from the best one?"
- After generation: "Video is ready! Open Publish to review/approve it, or want to generate another variation?"
- Periodically: "Have you considered exploring [related niche]? It's trending right now."

### Optional: Quick-Reply Chips
- When it helps the user pick a next step, you MAY append a single `<quick_replies>` block containing a JSON array of up to 6 short reply strings (each under ~80 chars). Each becomes a tappable chip; clicking one sends that exact text back as the user's next message.
- Put it at the very END of your response (after any `<action>` block). Keep labels short and phrased as things the user would say.
- Especially useful right after you ASK a clarifying question (e.g. "which platform?") — offer the likely answers as chips instead of leaving the user to type.
- Example:
```
<quick_replies>["Scout YouTube", "Scout TikTok", "Both"]</quick_replies>
```

## Available Actions

Output these JSON blocks at the END of your response to trigger actions:

```
<action>{{"type": "start_scout", "niche": "personal finance", "platforms": ["youtube", "tiktok", "douyin"]}}</action>
<action>{{"type": "analyze_channel", "url": "https://youtube.com/@ChannelName"}}</action>
<action>{{"type": "download_url", "url": "https://youtube.com/watch?v=xxx", "title": "optional title"}}</action>
<action>{{"type": "download_channel_videos", "url": "https://youtube.com/@ChannelName", "max_videos": 5}}</action>
<action>{{"type": "start_download", "scout_result_ids": ["id1", "id2"]}}</action>
<action>{{"type": "start_generate", "downloaded_video_id": "uuid"}}</action>
<action>{{"type": "start_upload", "generated_video_id": "uuid", "platforms": ["youtube", "tiktok", "facebook", "instagram"]}}</action>
Use `start_upload` only for a GeneratedVideo that the user has already explicitly approved in Publish. Never infer approval from "looks good", generation success, or a prior upload.
<action>{{"type": "start_wizard", "wizard_id": "youtube_auth"}}</action>
<action>{{"type": "show_scout_results"}}</action>
<action>{{"type": "show_downloaded"}}</action>
<action>{{"type": "show_videos"}}</action>
<action>{{"type": "show_video", "generated_video_id": "uuid"}}</action>
<action>{{"type": "show_publish_status", "generated_video_id": "uuid"}}</action>
<action>{{"type": "show_performance", "generated_video_id": "uuid"}}</action>
<action>{{"type": "show_youtube_performance"}}</action>
<action>{{"type": "show_outperforming_videos"}}</action>
<action>{{"type": "show_latest_performance_snapshot"}}</action>
<action>{{"type": "show_job_status", "job_id": "uuid"}}</action>
For these factual queries, never invent metrics, approval, or job states: the action returns the authoritative result. Do not add numeric claims in prose.
<action>{{"type": "content_calendar", "days": 7}}</action>
```

### News Research Actions (NEW — intelligent news scouting)

You can research trending news and articles from the web:
- Search 12 sources: Google News, Bing News, Hacker News, Reddit, CNBC, BBC, Reuters, NY Times, The Guardian, Al Jazeera, TechCrunch, Yahoo News
- AI deeply analyzes each article: hook, video angle, talking points, key quotes
- User saves best articles to Library → generates video scripts from them
- Perfect for: news commentary, hot takes, explainers, weekly recaps, reaction content
- You do NOT need to specify sources — all 12 are searched by default

```
<action>{{"type": "start_news_scout", "query": "AI regulation", "expanded_queries": ["EU AI Act 2026", "OpenAI regulation news"]}}</action>
<action>{{"type": "analyze_url", "url": "https://cnbc.com/some-article"}}</action>
<action>{{"type": "save_news_to_library", "article_ids": ["id1", "id2"]}}</action>
```

IMPORTANT news intelligence rules:
- If the user says "scout trending news" or "find news" WITHOUT a specific topic, you MUST ask what topic they want BEFORE scouting. Do NOT pick a topic yourself. Just ask: "What topic should I research? For example: crypto, politics, AI regulation, climate change..."
- If the user's query is vague (just "trending", "news", "latest"), ask ONE clarifying question BEFORE scouting. Do NOT emit an action block.
- If the user pastes a direct article URL, analyze that single article (use `analyze_url`)
- If the input is gibberish, politely ask what topic they want
- Expand vague queries into 2-3 specific search terms in `expanded_queries`
- After showing results, proactively suggest: "Want me to save the top 3?" or "Generate a video from the best one?"

PROACTIVE news behavior:
- If user casually mentions a topic/niche: "Want me to find today's trending news about [topic]? Great for commentary videos."
- After video scouting: "I also found breaking news related to [niche] — want me to pull the top stories?"
- When user saves articles but doesn't generate: "You've got [N] articles saved — the [best one] has strong video potential."
- Be a content strategist, not a passive tool.

### When to Use Which Action

- `analyze_channel` — User shares a channel/playlist URL (/@, /channel/, /c/, /playlist) and wants to understand it. ALWAYS use this first for channel URLs. Never jump straight to downloading an entire channel.
- `download_url` — User shares a SINGLE video URL (youtube.com/watch?v=xxx) and wants to download/analyze it.
- `download_channel_videos` — User has ALREADY seen the channel analysis summary and explicitly asks to download videos from that channel. Only use AFTER analyze_channel.
- `start_scout` — User wants to search by niche/topic across platforms. You can use ANY platform name in the platforms list — the system handles it dynamically. Never refuse a platform.
- `start_download` — Download specific scout results by ID.
- `show_downloaded` — User asks about their downloaded/analyzed videos. Shows the list inline in chat with generate buttons.
- `show_videos` — User asks about generated assets. Return the canonical recent GeneratedVideo list in chat.
- `show_video` / `show_publish_status` — Use an exact generated_video_id; if ambiguous, ask which video rather than guessing.
- `show_performance` — Exact generated_video_id; `show_youtube_performance` — published YouTube posts; `show_latest_performance_snapshot` — last measured post; `show_outperforming_videos` — observed top views, NOT a proven winner without comparable publish windows.
- `show_job_status` — Exact job_id; the backend provides the persisted status and actual error.
- `start_wizard` — Set up missing credentials when user agrees.
- `content_calendar` — User asks to "plan my content", "what should I post this week", etc. Generates an AI-powered day-by-day plan.

### CRITICAL: Think Before Acting
- When a user asks to "analyze" or "look at" a channel, FIRST present a lightweight overview. Do NOT download videos immediately.
- When a user shares a channel URL, use `analyze_channel` to show them what's there. Then ask what they want to do next.
- Only use `download_url` or `download_channel_videos` when the user explicitly wants to download specific videos.
- Be intelligent: gather lightweight info first → present summary → let user decide on heavy operations.

## Important: wizard_id values
Valid wizard IDs: youtube_auth, tiktok_upload_auth, telegram
Note: Scouting credentials (YouTube API key, TikHub token, Pexels) are configured via .env file or Settings page. If a platform's key is missing, that platform is skipped gracefully.

## ═══════ DYNAMIC CONTEXT (changes per request) ═══════

## User Profile (AI-generated from behavior patterns — use this to personalize)
{user_profile}

## Previous Session (what the user was doing last time — reference naturally if relevant)
{previous_session}

## Recent Failures (mention these proactively if relevant)
{recent_failures}

## User Context (current session stats)
{user_context}

## Live Pipeline State (where the user is in the funnel RIGHT NOW — be proactive about `next_best_action`)
{pipeline_state}

## Credential Status (what's configured vs missing)
{credential_status}

## Performance Insights (what content works best for this creator — use to guide recommendations)
{performance_insights}

## Smart Suggestions (offer these naturally, don't list them robotically)
{smart_suggestions}

## News Memory Context (user's news scouting history)
{news_context}
"""

ACTION_PATTERN = re.compile(r"<action>(.*?)</action>", re.DOTALL)
FACTUAL_ACTIONS = {
    "show_videos", "show_video", "show_publish_status", "show_performance",
    "show_youtube_performance", "show_outperforming_videos",
    "show_latest_performance_snapshot", "show_job_status",
}


def _has_factual_action(blocks: list[str]) -> bool:
    """F3 actions render only backend-owned facts, never model prose."""
    for block in blocks:
        try:
            action = json.loads(block.strip())
        except (TypeError, ValueError):
            continue
        if isinstance(action, dict) and action.get("type") in (FACTUAL_ACTIONS | {"start_upload"}):
            return True
    return False

# Quick-reply chips — the AI optionally appends a JSON array of short reply
# strings inside <quick_replies>...</quick_replies>. Each becomes a clickable
# chip below the assistant's bubble; clicking a chip submits the same text as a
# normal user message (no special dispatch). Chips are parsed AFTER the response
# streams so we can bundle them onto chat_done atomically — avoiding the race
# where the message renders before its chips arrive.
QUICK_REPLIES_PATTERN = re.compile(r"<quick_replies>(.*?)</quick_replies>", re.DOTALL)
_MAX_QUICK_REPLIES = 6
_MAX_QUICK_REPLY_LEN = 80


def _parse_quick_replies(full_response: str) -> list[str]:
    """Extract and sanitize the AI's <quick_replies> JSON array.

    Returns [] on any failure (missing block, malformed JSON, wrong shape) —
    quick replies are pure UX sugar; never block the response on parse errors.
    """
    match = QUICK_REPLIES_PATTERN.search(full_response)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(1).strip())
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s[:_MAX_QUICK_REPLY_LEN])
        if len(out) >= _MAX_QUICK_REPLIES:
            break
    return out


# Dispatch-time follow-up sink. When an action handler needs to say something to
# the user AFTER chat_done has fired (ask a clarifying question, report a no-op,
# note a skipped platform), it must NOT emit a bare `chat_token`: on the web
# client a chat_token flips isStreaming=true and, with no matching chat_done, the
# composer stays locked forever — the bot asks "which platform?" and the user
# can't answer. `_followup` instead emits a self-contained `assistant_message`
# WS event (rendered as a normal assistant bubble, optionally with quick-reply
# chips) AND appends the text to this contextvar sink so the caller can fold it
# into the persisted assistant turn — otherwise the model has no record it asked,
# and the user's next reply lands with no context. The messaging path (no live
# WS) relies on the sink alone: the follow-up text is appended to the returned
# reply.
_followup_sink: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "planner_followup_sink", default=None,
)


class PlannerAgent:
    def __init__(self):
        self.intelligence = UserIntelligence()

    async def _followup(self, user_id: str, text: str, quick_replies: list[str] | None = None):
        """Send a dispatch-time message to the user as a real assistant turn
        (never a bare chat_token — see the sink comment above). Also records it
        so the caller can persist it into the turn's assistant message."""
        text = (text or "").strip()
        if not text:
            return
        await ws_manager.send({
            "type": "assistant_message",
            "content": text,
            "quick_replies": quick_replies or [],
        }, user_id)
        sink = _followup_sink.get()
        if sink is not None:
            sink.append(text)

    async def _gather_common_context(self, user_settings, user_id: str) -> dict:
        """Fetch the context pieces shared by BOTH the streaming WS path
        (handle_message) and the messaging path (handle_message_text).

        Centralizing these kills the drift that used to bite when a shared piece
        was added: the user-intel summary, smart suggestions, credential map,
        profile, recent-failures, performance insights, news context, and live
        funnel state were hand-duplicated in both handlers and could silently
        diverge. The path-specific RENDERING (profile dump vs one-line, indent
        style, cookie-age warnings on the web credential block, previous-session
        text) stays in each handler, where it legitimately differs.

        Returns the raw fetched pieces; each caller formats them as it needs.
        """
        ctx = await self.intelligence.get_context_summary(user_id)
        suggestions = await self.intelligence.get_smart_suggestions(user_id)
        cred_status = await self.intelligence.get_credential_status(user_settings, user_id)
        user_profile = await self.intelligence.get_user_profile(user_id)
        recent_failures = await self.intelligence.get_recent_failures(user_id)
        perf_insights = await self.intelligence.get_performance_insights(user_id)
        news_ctx = await self.intelligence.get_news_context(user_id)
        pipeline = await self.intelligence.get_pipeline_state(user_id)

        failures_text = "\n".join(f"  - {f}" for f in recent_failures) if recent_failures else "None"

        return {
            "ctx": ctx,
            "suggestions": suggestions,
            "cred_status": cred_status,
            "user_profile": user_profile,
            "failures_text": failures_text,
            "perf_insights": perf_insights,
            "news_ctx": news_ctx,
            "pipeline": pipeline,
        }

    async def handle_message(
        self,
        message: str,
        history: list[dict],
        user_settings,
        user_id: str = "local",
        previous_session_context: list[dict] = None,
    ):
        """
        Main handler: stream AI response over WS, then dispatch any <action> blocks.
        history: list of {"role": "user"|"assistant", "content": "..."}
        previous_session_context: last few messages from the user's previous session (for continuity)
        """
        logger.info("PLANNER handle_message | user=%s | msg=%s", user_id, message[:100])

        # Shared fetches (user-intel summary, suggestions, credential map,
        # profile, recent failures, perf, news, funnel state) — built once in a
        # helper so the WS and messaging paths can't drift on what they read.
        # Path-specific RENDERING (cookie-age warnings, full JSON dumps,
        # previous-session text) stays below, where it legitimately differs.
        common = await self._gather_common_context(user_settings, user_id)
        ctx = common["ctx"]
        suggestions = common["suggestions"]
        cred_status = common["cred_status"]
        user_profile = common["user_profile"]
        failures_text = common["failures_text"]
        perf_insights = common["perf_insights"]
        news_ctx = common["news_ctx"]
        pipeline = common["pipeline"]

        # Format credential status with health warnings (web-only enrichment)
        cred_lines = []
        cred_warnings = []
        for service, info in cred_status.items():
            status_str = "CONFIGURED" if info["configured"] else "NOT SET UP"
            wizard = info.get("setup_wizard", "")
            line = f"  {service}: {status_str}"
            if wizard:
                line += f" (wizard: {wizard})"
            # Add health warnings for cookies
            if info.get("age_days") is not None and info["age_days"] >= 25:
                days = info["age_days"]
                severity = "EXPIRED/CRITICAL" if days >= 30 else "EXPIRING SOON"
                line += f" ⚠️ {severity} ({days} days old)"
                cred_warnings.append(f"⚠️ {service} cookie is {days} days old — offer to refresh it via wizard")
            cred_lines.append(line)

        # Format user profile for the prompt
        profile_text = "No profile yet — this is a new or early user."
        if user_profile:
            profile_text = json.dumps(user_profile, indent=2, ensure_ascii=False)

        # Format previous session context
        prev_session_text = "None — this is the user's first session or a continuing session."
        if previous_session_context:
            prev_lines = []
            for m in previous_session_context:
                role = "User" if m["role"] == "user" else "You"
                prev_lines.append(f"  {role}: {m['content']}")
            prev_session_text = "\n".join(prev_lines)

        # Format performance insights
        perf_text = "No performance data yet — user hasn't uploaded enough videos."
        if perf_insights:
            perf_text = json.dumps(perf_insights, indent=2)

        # Build news memory context
        news_text = "No news scouting history yet."
        if news_ctx and news_ctx.get("total_news_scouts", 0) > 0:
            news_text = json.dumps(news_ctx, indent=2, ensure_ascii=False)

        # Live funnel state — where the user is right now (downloaded-not-generated,
        # generated-not-uploaded, etc.) + the single highest-value next step. This
        # is what makes the planner proactive instead of purely reactive.
        pipeline_text = json.dumps(pipeline, indent=2, ensure_ascii=False) if pipeline else "Unknown."

        system = SYSTEM_PROMPT.format(
            user_context=json.dumps(ctx, indent=2),
            pipeline_state=pipeline_text,
            smart_suggestions=json.dumps(suggestions),
            credential_status="\n".join(cred_lines),
            user_profile=profile_text,
            previous_session=prev_session_text,
            recent_failures=failures_text,
            performance_insights=perf_text,
            news_context=news_text,
        )

        # Build messages list
        messages = list(history)
        messages.append({"role": "user", "content": message})

        # Get AI client — BYOK from .env or per-user settings
        try:
            ai = get_ai_client(user_settings)
        except Exception:
            welcome = (
                "Affiliate2 AI chưa có nhà cung cấp AI hoạt động.\n\n"
                "To get started, set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in your `.env` file, "
                "or configure your provider and key in **Settings**."
            )
            await ws_manager.send({"type": "chat_token", "token": welcome}, user_id)
            await ws_manager.send({"type": "chat_done", "full_response": welcome}, user_id)
            return

        # Buffer until action blocks are known: streaming model tokens could
        # display invented metrics or a raw <action> before authoritative data arrives.
        full_response = ""
        async for token in ai.chat_stream(messages=messages, system=system, max_tokens=1024):
            full_response += token

        # Signal completion. Strip both <action> and <quick_replies> markers from
        # the user-visible text; the chips ride on chat_done as a structured
        # field so the frontend renders them as buttons, not raw markdown.
        # qr_stripped is the single source of truth for "response minus chip
        # markup" — action parsing + the safety net + clean_response all derive
        # from it, so an <action> substring inside a chip label can't dispatch.
        quick_replies = _parse_quick_replies(full_response)
        qr_stripped = QUICK_REPLIES_PATTERN.sub("", full_response)
        clean_response = ACTION_PATTERN.sub("", qr_stripped).strip()
        if _has_factual_action(ACTION_PATTERN.findall(qr_stripped)):
            clean_response = ""  # backend facts below, never model-invented counts
        await ws_manager.send({
            "type": "chat_done",
            "full_response": clean_response,
            "quick_replies": quick_replies,
        }, user_id)

        # Parse and dispatch action blocks (from the qr-stripped response, so
        # chip labels can never spawn phantom actions).
        actions = ACTION_PATTERN.findall(qr_stripped)
        logger.debug("PLANNER parsed %d action block(s) from AI response", len(actions))

        # Safety net: if AI talked about scouting but forgot the action block,
        # auto-trigger — UNLESS it deliberately offered quick replies (a
        # clarifying question is an intentional no-action turn; inferring one
        # here would clobber it).
        if not actions and not quick_replies:
            actions = self._infer_missing_action(message, qr_stripped)
            if actions:
                logger.info("PLANNER safety-net inferred action: %s", actions)

        # Collect any dispatch-time follow-ups (clarifying questions, no-op
        # notes) so they're folded into the persisted assistant turn — otherwise
        # the model has no memory it asked and the user's reply arrives context-less.
        followups: list[str] = []
        sink_token = _followup_sink.set(followups)
        try:
            for action_json in actions:
                try:
                    action = json.loads(action_json.strip())
                    await self._dispatch_action(action, user_settings, user_id)
                except json.JSONDecodeError as e:
                    logger.warning(f"Malformed action JSON, attempting AI repair: {action_json!r}")
                    try:
                        from backend.core.ai_retry import ai_fix_action
                        repaired = await ai_fix_action(action_json, str(e), user_settings)
                        if repaired:
                            await self._dispatch_action(repaired, user_settings, user_id)
                        else:
                            logger.error(f"AI could not repair action JSON: {action_json!r}")
                    except Exception as repair_err:
                        logger.error(f"Action repair failed: {repair_err}")
        finally:
            _followup_sink.reset(sink_token)

        if followups:
            joined = "\n".join(followups)
            clean_response = f"{clean_response}\n\n{joined}".strip() if clean_response else joined

        # Record conversation event
        await self.intelligence.record_event("chat_message", {
            "user_message": message[:200],
            "actions_triggered": [json.loads(a) for a in actions if self._is_valid_json(a)],
        }, user_id)

        return clean_response

    async def handle_message_text(
        self,
        message: str,
        user_settings,
        user_id: str = "local",
    ) -> str:
        """
        Non-streaming sibling of handle_message() for messaging channels
        (Telegram, WhatsApp, Discord, Slack) that can't push WebSocket tokens.
        Builds the same system prompt and dispatches the same <action> blocks,
        but returns the full response string with action blocks stripped.
        """
        logger.info("PLANNER handle_message_text | user=%s | msg=%s", user_id, message[:100])

        # Same shared fetches as handle_message (via the common helper so the
        # two surfaces can't drift). Messaging then renders the lean variants of
        # the divergent blocks (no cookie-age warnings, one-line-free profile,
        # un-indented funnel state).
        common = await self._gather_common_context(user_settings, user_id)
        ctx = common["ctx"]
        suggestions = common["suggestions"]
        cred_status = common["cred_status"]
        user_profile = common["user_profile"]
        failures_text = common["failures_text"]
        perf_insights = common["perf_insights"]
        news_ctx = common["news_ctx"]
        pipeline = common["pipeline"]

        cred_lines = []
        for service, info in cred_status.items():
            status_str = "CONFIGURED" if info["configured"] else "NOT SET UP"
            wizard = info.get("setup_wizard", "")
            line = f"  {service}: {status_str}"
            if wizard:
                line += f" (wizard: {wizard})"
            cred_lines.append(line)

        profile_text = (
            json.dumps(user_profile, indent=2, ensure_ascii=False)
            if user_profile else "No profile yet."
        )

        perf_text = json.dumps(perf_insights, indent=2) if perf_insights else "No performance data yet."

        news_text = (
            json.dumps(news_ctx, indent=2, ensure_ascii=False)
            if news_ctx and news_ctx.get("total_news_scouts", 0) > 0
            else "No news scouting history yet."
        )

        # Live funnel state — cheap (indexed counts) and high-value even for
        # messaging: it's how the bot proactively nudges the next step.
        pipeline_text = json.dumps(pipeline, ensure_ascii=False) if pipeline else "Unknown."

        system = SYSTEM_PROMPT.format(
            user_context=json.dumps(ctx, indent=2),
            pipeline_state=pipeline_text,
            smart_suggestions=json.dumps(suggestions),
            credential_status="\n".join(cred_lines),
            user_profile=profile_text,
            previous_session="N/A — inbound from messaging channel.",
            recent_failures=failures_text,
            performance_insights=perf_text,
            news_context=news_text,
        )

        try:
            ai = get_ai_client(user_settings)
        except Exception:
            return (
                "AI provider is not configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY "
                "in your .env, or configure your key in Settings."
            )

        try:
            full_response = await ai.chat(
                messages=[{"role": "user", "content": message}],
                system=system,
                max_tokens=1024,
            )
        except Exception as e:
            logger.exception("PLANNER text chat failed: %s", e)
            return "Sorry — I couldn't reach the AI backend just now. Try again in a moment."

        # Strip <quick_replies> markup too — messaging channels don't render
        # chips, so the block would otherwise leak into the plain-text reply.
        quick_replies = _parse_quick_replies(full_response)
        qr_stripped = QUICK_REPLIES_PATTERN.sub("", full_response)
        clean_response = ACTION_PATTERN.sub("", qr_stripped).strip()
        actions = ACTION_PATTERN.findall(qr_stripped)
        if _has_factual_action(actions):
            clean_response = ""  # channel response comes only from native services
        # Mirror the web path: a deliberate clarifying-question turn (chips, no
        # action block) must NOT get a premature action keyword-inferred.
        if not actions and not quick_replies:
            actions = self._infer_missing_action(message, qr_stripped)

        # Capture dispatch-time follow-ups via the sink. There's no live WS on
        # the messaging path, so the follow-up text is appended to the returned
        # reply (that's the whole point of the sink here).
        followups: list[str] = []
        sink_token = _followup_sink.set(followups)
        try:
            for action_json in actions:
                try:
                    action = json.loads(action_json.strip())
                    await self._dispatch_action(action, user_settings, user_id)
                except json.JSONDecodeError:
                    logger.warning("Malformed action JSON in text path: %r", action_json)
                except Exception as e:
                    logger.exception("Action dispatch failed in text path: %s", e)
        finally:
            _followup_sink.reset(sink_token)

        if followups:
            joined = "\n".join(followups)
            clean_response = f"{clean_response}\n\n{joined}".strip() if clean_response else joined

        await self.intelligence.record_event(
            "chat_message",
            {
                "user_message": message[:200],
                "source": "messaging",
                "actions_triggered": [json.loads(a) for a in actions if self._is_valid_json(a)],
            },
            user_id,
        )

        return clean_response or "Working on it. ✅"

    async def _show_native_fact(self, kind: str, action: dict, user_id: str):
        """Read canonical application state; messaging never owns a second store."""
        from sqlalchemy import select
        from backend.database import AsyncSessionLocal
        from backend.models.generated_video import GeneratedVideo
        from backend.models.job import Job

        if kind == "show_job_status":
            job_id = action.get("job_id")
            if not isinstance(job_id, str) or not job_id.strip():
                await self._followup(user_id, "A valid job_id is required.")
                return
            async with AsyncSessionLocal() as db:
                job = await db.get(Job, job_id)
                if job is None or job.user_id != user_id:
                    await self._followup(user_id, f"Job {job_id} not found.")
                    return
                await self._followup(user_id,
                    f"Job {job.id} ({job.job_type}): {job.status}. "
                    f"Progress: {job.progress_pct}%. "
                    f"Error: {job.error_message or 'none reported'}."
                )
            return

        if kind in {"show_video", "show_publish_status", "show_performance"}:
            video_id = action.get("generated_video_id")
            if not isinstance(video_id, str) or not video_id.strip():
                await self._followup(user_id, "A valid generated_video_id is required; select a generated asset first.")
                return
            async with AsyncSessionLocal() as db:
                video = await db.get(GeneratedVideo, video_id)
                if video is None or video.user_id != user_id:
                    await self._followup(user_id, f"Generated video {video_id} not found.")
                    return
                summary = (f"{video.title or 'Untitled'} (GeneratedVideo {video.id}): "
                           f"status {video.status}; publish approval "
                           f"{'approved' if video.publish_approved_at else 'not approved'}.")
                try:
                    destinations = json.loads(video.uploaded_platforms_json or "[]")
                except (TypeError, ValueError):
                    destinations = []
                if not isinstance(destinations, list):
                    destinations = []
                if kind == "show_publish_status":
                    await self._followup(user_id, summary +
                        f" Published destinations: {', '.join(destinations) if destinations else 'none'}; "
                        f"YouTube content ID: {video.youtube_video_id or 'unavailable'}. "
                        "A publish transaction ID is not a public post ID.")
                    return
                if kind == "show_video":
                    await self._followup(user_id, summary)
                    return
            # Ownership has been checked before calling the native Performance owner.
            from backend.services.performance_tracker import get_video_performance
            data = await get_video_performance(video_id)
            if not data["latest"]:
                await self._followup(user_id, summary + " Performance: insufficient data (no snapshot).")
                return
            parts = [summary]
            for platform, snapshot in data["latest"].items():
                fmt = lambda value: "unavailable" if value is None else str(value)
                parts.append(f"{platform}: views {fmt(snapshot['views'])}, "
                             f"likes {fmt(snapshot['likes'])}, comments {fmt(snapshot['comments'])}; "
                             f"last sync {snapshot['fetched_at'] or 'unavailable'}; "
                             f"{'stale' if snapshot['stale'] else 'current'}; "
                             f"{snapshot['state']}.")
            await self._followup(user_id, "\n".join(parts))
            return

        if kind == "show_videos":
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(GeneratedVideo).where(
                    GeneratedVideo.user_id == user_id).order_by(
                    GeneratedVideo.created_at.desc()).limit(10))).scalars().all()
            await self._followup(user_id, "Recent generated assets:\n" + "\n".join(
                f"{row.id} — {row.title or 'Untitled'} ({row.status}; "
                f"approval {'approved' if row.publish_approved_at else 'not approved'})"
                for row in rows) if rows else "No generated assets found.")
            return

        from backend.services.performance_tracker import get_performance_overview
        overview = await get_performance_overview(user_id)
        posts = overview["posts"]
        if kind == "show_youtube_performance":
            posts = [post for post in posts if post["platform"] == "youtube"]
        elif kind == "show_latest_performance_snapshot":
            posts = [post for post in posts if post["latest_metrics"]]
            posts.sort(key=lambda post: post["latest_metrics"]["fetched_at"] or "", reverse=True)
            posts = posts[:1]
        elif kind == "show_outperforming_videos":
            posts = overview["top_posts"]
        else:
            posts = posts[:10]
        if not posts:
            await self._followup(user_id, "Insufficient performance data: no matching published posts or snapshots.")
            return
        lines = []
        for post in posts[:10]:
            m = post["latest_metrics"]
            views = "unavailable" if m is None or m["views"] is None else m["views"]
            sync = m["fetched_at"] if m else "unavailable"
            stale = "; stale" if m and m["stale"] else ""
            lines.append(f"{post['generated_video_id']} — {post['title']} [{post['platform']}]: "
                         f"views {views}; last sync {sync}{stale}; {post['state']}.")
        prefix = ("Highest observed views (not a proven outperforming claim without comparable "
                  "publish windows):\n" if kind == "show_outperforming_videos" else "Performance snapshots:\n")
        await self._followup(user_id, prefix + "\n".join(lines))

    async def _dispatch_action(self, action: dict, user_settings, user_id: str):
        if not isinstance(action, dict):
            await self._followup("Invalid action: expected an object.")
            return
        action_type = action.get("type")
        logger.info("DISPATCH action=%s | user=%s | payload=%s", action_type, user_id, json.dumps(action, ensure_ascii=False)[:200])
        if action_type not in PLANNER_ACTION_TYPES:
            await self._followup(
                user_id,
                f"Affiliate2 không có action backend '{action_type}'. Không có thao tác nào được thực hiện.",
            )
            return
        if action_type in FACTUAL_ACTIONS:
            await self._show_native_fact(action_type, action, user_id)
            return

        if action_type == "start_scout":
            await self._check_and_start_scout(action, user_settings, user_id)

        elif action_type == "analyze_channel":
            await self._analyze_channel(action, user_id)

        elif action_type == "download_channel_videos":
            await self._download_channel_videos(action, user_id)

        elif action_type == "download_url":
            await self._download_url(action, user_settings, user_id)

        elif action_type == "start_wizard":
            wizard_id = action.get("wizard_id")
            if wizard_id in WIZARDS:
                await ws_manager.send({
                    "type": "wizard_start",
                    "wizard_id": wizard_id,
                    "wizard": WIZARDS[wizard_id],
                }, user_id)

        elif action_type == "start_download":
            scout_result_ids = action.get("scout_result_ids", [])
            if scout_result_ids:
                from backend.agents.job_helper import create_job
                from backend.core.task_runner import run_download, dispatch
                job = await create_job("download", user_id, {"scout_result_ids": scout_result_ids})
                dispatch(run_download(job_id=job.id, scout_result_ids=scout_result_ids, user_id=user_id))
                await ws_manager.send({
                    "type": "job_started",
                    "job_id": job.id,
                    "job_type": "download",
                    "message": f"Downloading {len(scout_result_ids)} videos...",
                }, user_id)

        elif action_type == "start_generate":
            downloaded_video_id = action.get("downloaded_video_id")
            if downloaded_video_id:
                from backend.agents.job_helper import create_job
                from backend.core.task_runner import run_generate, dispatch
                job = await create_job("generate", user_id, {"downloaded_video_id": downloaded_video_id})
                dispatch(run_generate(
                    job_id=job.id, downloaded_video_id=downloaded_video_id, user_id=user_id,
                ))

        elif action_type == "start_upload":
            video_id = action.get("generated_video_id")
            if not isinstance(video_id, str) or not video_id.strip():
                await self._followup(user_id, "A valid generated_video_id is required; no publish job started.")
                return
            from fastapi import HTTPException
            from backend.api.videos import upload_video
            from backend.database import AsyncSessionLocal
            from backend.models.generated_video import GeneratedVideo
            async with AsyncSessionLocal() as db:
                row = await db.get(GeneratedVideo, video_id)
                if row is None or row.user_id != user_id:
                    await ws_manager.send_constraint_warning(
                        constraint="publish_video_missing", message="Generated video not found.",
                        severity="error", user_id=user_id,
                    )
                    await self._followup(user_id, "That generated video was not found; no publish job started.")
                    return
                try:
                    result = await upload_video(video_id, {
                        "platforms": action.get("platforms", ["youtube"]),
                    }, db=db)
                except HTTPException as exc:
                    detail = str(exc.detail)
                    constraint = ("publish_approval" if exc.status_code == 403
                                  else "publish_platforms" if exc.status_code == 400 and "platform" in detail.lower()
                                  else "publish_account" if "Disconnected" in detail
                                  else "publish_preflight")
                    await ws_manager.send_constraint_warning(
                        constraint=constraint, message=detail,
                        severity="warning" if exc.status_code == 403 else "error",
                        user_id=user_id,
                    )
                    await self._followup(user_id,
                        f"I didn't publish it: {detail}" if exc.status_code == 403
                        else f"No publish job started: {detail}")
                    return
            await self._followup(user_id,
                f"Publish job {result['job_id']} started for GeneratedVideo {video_id}. "
                "Check job status for the provider result; queued is not published.")

        elif action_type == "show_scout_results":
            await self._show_scout_results(action, user_id)

        elif action_type == "show_downloaded":
            await self._show_downloaded(user_id)

        elif action_type == "show_videos":
            await self._show_videos(user_id)

        elif action_type == "content_calendar":
            # Coerce: the model supplies this and it's used as an integer
            # downstream, so a `"seven"` raised TypeError straight out of the
            # dispatcher and took the whole chat turn with it. Every other
            # action here either ignores a wrongly-typed payload or degrades;
            # this was the only unguarded coercion.
            try:
                days = int(action.get("days", 7))
            except (TypeError, ValueError):
                days = 7
            days = max(1, min(days, 90))
            calendar = await self.intelligence.generate_content_calendar(user_id, days)
            if calendar:
                await ws_manager.send({
                    "type": "content_calendar",
                    "calendar": calendar,
                }, user_id)
            else:
                await self._followup(
                    user_id,
                    "I need more data to generate a personalized content calendar. Try uploading a few videos first so I can learn what works for your audience.",
                )

        elif action_type == "start_news_scout":
            await self._start_news_scout(action, user_id)

        elif action_type == "analyze_url":
            await self._analyze_article_url(action, user_id)

        elif action_type == "save_news_to_library":
            await self._save_news_to_library(action, user_id)

    async def _start_news_scout(self, action: dict, user_id: str):
        """Start a news scout job."""
        query = action.get("query", "")
        if not query:
            return
        expanded_queries = action.get("expanded_queries", [])
        sources = action.get("sources")  # None = all 12 sources (default in scraper)

        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_news_scout, dispatch

        job = await create_job("news_scout", user_id, {
            "query": query,
            "expanded_queries": expanded_queries,
            "sources": sources,
        })
        dispatch(run_news_scout(
            job_id=job.id, query=query,
            expanded_queries=expanded_queries or None,
            sources=sources or None,
            user_id=user_id,
        ))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "news_scout",
            "message": f"Researching '{query}' across {', '.join(sources) if sources else 'all 12 news sources'}...",
        }, user_id)
        await self.intelligence.record_event("news_scouted", {
            "query": query,
            "expanded_queries": expanded_queries,
            "sources": sources,
        }, user_id)

    async def _analyze_article_url(self, action: dict, user_id: str):
        """Analyze a single article URL."""
        url = action.get("url", "").strip()
        if not url:
            return

        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_news_scout, dispatch

        job = await create_job("news_scout", user_id, {"direct_url": url})
        dispatch(run_news_scout(
            job_id=job.id, query="direct URL analysis",
            direct_url=url, user_id=user_id,
        ))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "news_scout",
            "message": "Analyzing article...",
        }, user_id)

    async def _save_news_to_library(self, action: dict, user_id: str):
        """Save selected news articles to Library."""
        article_ids = action.get("article_ids", [])
        if not article_ids:
            return

        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_news_save, dispatch

        job = await create_job("news_save", user_id, {"article_ids": article_ids})
        dispatch(run_news_save(job_id=job.id, article_ids=article_ids, user_id=user_id))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "news_save",
            "message": f"Saving {len(article_ids)} article{'s' if len(article_ids) != 1 else ''} to Library...",
        }, user_id)
        await self.intelligence.record_event("news_saved", {
            "article_ids": article_ids,
            "count": len(article_ids),
        }, user_id)

    async def _show_scout_results(self, action: dict, user_id: str):
        """Fetch recent scout results from DB and send them over WS."""
        from backend.database import AsyncSessionLocal
        from backend.models.scout_result import ScoutResult
        from sqlalchemy import select

        job_id = action.get("job_id")
        limit = action.get("limit", 50)

        async with AsyncSessionLocal() as db:
            query = (
                select(ScoutResult)
                .where(ScoutResult.user_id == user_id)
                .order_by(ScoutResult.created_at.desc())
                .limit(limit)
            )
            if job_id:
                query = query.where(ScoutResult.job_id == job_id)
            result = await db.execute(query)
            results = result.scalars().all()

        if not results:
            await self._followup(user_id, "No scout results found yet. Try scouting a niche first!")
            return

        payload = [{
            "id": r.id,
            "platform": r.platform,
            "video_id": r.video_id,
            "title": r.title,
            "author": r.author,
            "author_url": r.author_url,
            "views": r.views,
            "likes": r.likes,
            "comments": r.comments,
            "shares": r.shares,
            "duration_seconds": r.duration_seconds,
            "upload_date": r.upload_date.isoformat() if r.upload_date else None,
            "virality_score": r.virality_score,
            "views_per_hour": r.views_per_hour,
            "outlier_score": r.outlier_score,
            "thumbnail_url": r.thumbnail_url,
            "video_url": r.video_url,
            "embed_url": r.embed_url,
        } for r in results]

        try:
            from backend.affiliate.radar.service import enrich_scout_payload
            payload = await asyncio.to_thread(enrich_scout_payload, payload)
        except Exception as exc:
            logger.warning("Chat scout Radar enrichment skipped (non-fatal): %s", exc)

        platforms = {}
        for item in payload:
            platforms.setdefault(item["platform"], []).append(item)

        for platform, items in platforms.items():
            await ws_manager.send({
                "type": "scout_results",
                "job_id": job_id or "",
                "platform": platform,
                "total": len(items),
                "results": items,
            }, user_id)

    async def _show_downloaded(self, user_id: str):
        """Fetch downloaded videos from DB and send them as a rich list in chat."""
        from backend.database import AsyncSessionLocal
        from backend.models.downloaded_video import DownloadedVideo
        from backend.models.scout_result import ScoutResult
        from sqlalchemy import select, outerjoin
        import json as _json

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DownloadedVideo)
                .where(DownloadedVideo.user_id == user_id)
                .order_by(DownloadedVideo.created_at.desc())
                .limit(20)
            )
            downloads = result.scalars().all()

            # Fetch associated scout results for titles/thumbnails
            scout_ids = [d.scout_result_id for d in downloads if d.scout_result_id]
            scouts_map = {}
            if scout_ids:
                sr = await db.execute(
                    select(ScoutResult).where(ScoutResult.id.in_(scout_ids))
                )
                for s in sr.scalars().all():
                    scouts_map[s.id] = s

        if not downloads:
            await self._followup(
                user_id,
                "No downloaded videos yet. Try scouting a niche and downloading some videos first!",
            )
            return

        items = []
        for d in downloads:
            scout = scouts_map.get(d.scout_result_id)
            insights = _json.loads(d.insights_json) if d.insights_json else {}
            items.append({
                "id": d.id,
                "title": (scout.title if scout else None) or d.video_path or "Untitled",
                "thumbnail_url": scout.thumbnail_url if scout else None,
                "platform": scout.platform if scout else None,
                "views": scout.views if scout else None,
                "duration_seconds": d.duration_seconds,
                "transcript_preview": (d.transcript or "")[:150],
                "has_insights": bool(d.insights_json),
                "suggested_angle": insights.get("suggested_angle", ""),
                "created_at": d.created_at.isoformat() if d.created_at else None,
            })

        await ws_manager.send({
            "type": "downloaded_list",
            "total": len(items),
            "videos": items,
        }, user_id)

    async def _show_videos(self, user_id: str):
        """Send a nudge to navigate to videos page."""
        await ws_manager.send({
            "type": "action",
            "action": {"type": "navigate", "path": "/videos"},
        }, user_id)

    async def _analyze_channel(self, action: dict, user_id: str):
        """Lightweight channel analysis — fetch metadata + video list without downloading."""
        url = action.get("url", "").strip()
        if not url:
            return

        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_analyze_channel, dispatch

        job = await create_job("analyze", user_id, {"url": url, "type": "channel_analysis"})
        dispatch(run_analyze_channel(job_id=job.id, url=url, user_id=user_id))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "analyze",
            "message": f"Analyzing channel...",
        }, user_id)

    async def _download_channel_videos(self, action: dict, user_id: str):
        """Download top N videos from a channel after user has seen the analysis."""
        url = action.get("url", "").strip()
        max_videos = action.get("max_videos", 5)
        if not url:
            return

        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_download_url, dispatch

        job = await create_job("download", user_id, {"url": url, "channel_download": True, "max_videos": max_videos})
        dispatch(run_download_url(job_id=job.id, url=url, title="", user_id=user_id))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "download",
            "message": f"Downloading top {max_videos} videos from channel...",
        }, user_id)

    async def _download_url(self, action: dict, _user_settings, user_id: str):
        """Download a video directly from a URL provided by the user."""
        url = action.get("url", "").strip()
        title = action.get("title", "")

        if not url:
            return

        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_download_url, dispatch

        job = await create_job("download", user_id, {
            "url": url,
            "title": title,
            "direct_url": True,
        })
        dispatch(run_download_url(job_id=job.id, url=url, title=title, user_id=user_id))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "download",
            "message": f"Downloading video from URL...",
        }, user_id)

    # Platforms that need API credentials — keys come from .env (BYOK).
    _CREDENTIAL_PLATFORMS = {
        "youtube": {
            "check": lambda us: bool(settings.YOUTUBE_API_KEY),
        },
        "tiktok": {
            "check": lambda us: (
                bool(settings.TIKHUB_API_KEY)
                or (us and us.tiktok_cookie_encrypted)
            ),
        },
    }

    async def _check_and_start_scout(self, action: dict, user_settings, user_id: str):
        """Start a scout job. Unavailable platforms are skipped gracefully."""
        niche = action.get("niche", "")
        raw_platforms = action.get("platforms", ["youtube"])
        if isinstance(raw_platforms, str):
            raw_platforms = [raw_platforms]
        elif not isinstance(raw_platforms, (list, tuple)):
            raw_platforms = []
        platforms = [
            platform.strip().lower()
            for platform in raw_platforms
            if isinstance(platform, str) and platform.strip()
        ] or ["youtube"]

        ready_platforms = []
        skipped = []

        for platform in platforms:
            cred_info = self._CREDENTIAL_PLATFORMS.get(platform)
            if cred_info:
                if cred_info["check"](user_settings):
                    ready_platforms.append(platform)
                else:
                    skipped.append(platform)
            else:
                ready_platforms.append(platform)

        if not ready_platforms:
            await self._followup(
                user_id,
                "No platforms available for scouting — please configure API keys in Settings.",
            )
            return

        if skipped:
            await self._followup(
                user_id,
                f"Note: {', '.join(skipped)} unavailable (no API key configured) — "
                f"scouting on {', '.join(ready_platforms)} only.",
            )

        # Kick off scout
        from backend.agents.job_helper import create_job
        from backend.core.task_runner import run_scout, dispatch
        job = await create_job("scout", user_id, {"niche": niche, "platforms": ready_platforms})
        dispatch(run_scout(job_id=job.id, niche=niche, platforms=ready_platforms, user_id=user_id))
        await ws_manager.send({
            "type": "job_started",
            "job_id": job.id,
            "job_type": "scout",
            "message": f"Scouting '{niche}' on {', '.join(ready_platforms)}...",
        }, user_id)
        await self.intelligence.record_event("niche_searched", {"niche": niche, "platforms": ready_platforms}, user_id)

    @staticmethod
    def _infer_missing_action(user_message: str, ai_response: str) -> list[str]:
        """
        Safety net: if the AI clearly intended to scout/search but forgot the <action> block,
        infer the action from the user message. Returns list of action JSON strings.
        """
        msg_lower = user_message.lower()
        resp_lower = ai_response.lower()

        # Detect direct article URL — always trigger analyze_url
        import re as _re
        url_match = _re.search(r'(https?://[^\s<>"\']+)', user_message)
        if url_match:
            url = url_match.group(1).rstrip(".,;:)")
            # Check if it's a news/article URL (not a YouTube/TikTok/Douyin video)
            video_domains = ["youtube.com", "youtu.be", "tiktok.com", "douyin.com"]
            is_video_url = any(d in url.lower() for d in video_domains)
            if not is_video_url:
                return [json.dumps({"type": "analyze_url", "url": url})]

        # Detect news scout intent
        news_keywords = ["news", "article", "headlines", "新闻", "热点", "资讯"]
        has_news_intent = any(kw in msg_lower for kw in news_keywords)

        # Detect scout intent from user message
        scout_keywords = [
            "scout", "search", "find", "look for", "trending",
            "找", "搜索", "搜一下", "查找", "热门", "帮我找",
        ]
        has_scout_intent = any(kw in msg_lower for kw in scout_keywords)

        # Detect that AI claimed it was doing something
        doing_keywords = [
            "searching", "scouting", "looking", "on it", "i'll search", "let me find",
            "正在搜索", "正在为您", "开始搜索", "马上", "开始为您",
        ]
        ai_claimed_action = any(kw in resp_lower for kw in doing_keywords)

        if has_scout_intent and ai_claimed_action:
            # Extract niche from user message — strip common prefixes
            niche = user_message.strip()
            for prefix in ["scout ", "search ", "find ", "look for ", "找一下", "找", "搜索", "搜一下", "帮我找", "查找"]:
                if niche.lower().startswith(prefix):
                    niche = niche[len(prefix):].strip()
                    break

            if niche:
                # If the user mentioned "news"/"article", use news scout
                if has_news_intent:
                    # Strip "news" from the query for cleaner search
                    clean_query = niche
                    for word in ["news", "articles", "headlines", "新闻", "热点", "资讯"]:
                        clean_query = clean_query.replace(word, "").strip()
                    clean_query = clean_query or niche
                    # Don't auto-trigger for vague queries like "trending news" — let AI ask
                    vague_queries = {"trending", "trending news", "latest", "latest news", "news", "headlines"}
                    if clean_query.lower() in vague_queries:
                        return []  # Let the AI ask what topic
                    action = json.dumps({
                        "type": "start_news_scout",
                        "query": clean_query,
                    })
                    return [action]

                action = json.dumps({
                    "type": "start_scout",
                    "niche": niche,
                    "platforms": ["youtube", "tiktok"],
                })
                return [action]

        return []

    @staticmethod
    def _is_valid_json(s: str) -> bool:
        try:
            json.loads(s)
            return True
        except Exception:
            return False
