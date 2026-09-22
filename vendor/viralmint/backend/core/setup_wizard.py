# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
WIZARDS = {
    "douyin_cookie": {
        "title": "Douyin Cookie Setup (Advanced — at your own risk)",
        "description": "Fallback for scouting Douyin trending videos. Uses your logged-in Douyin sessionid, which means Douyin sees the requests as coming from your account. This is against Douyin's Terms of Service and may result in your account being throttled or suspended. There is no official Douyin developer API for non-mainland-China developers, so no fully sanctioned alternative exists for scouting Douyin from outside China.",
        "risk_level": "high",
        "steps": [
            {"id": 1, "instruction": "Read this carefully: using your Douyin sessionid violates Douyin's Terms of Service. ViralMint is not affiliated with Douyin and the maintainers are not responsible for any consequences to your account. Do not proceed if you are unwilling to accept that risk.", "action": "wait_confirm", "confirm_label": "I understand and accept the risk"},
            {"id": 2, "instruction": "Open Chrome and go to Douyin", "action": "open_url", "url": "https://www.douyin.com"},
            {"id": 3, "instruction": "Log in to your Douyin account (or create a free one)", "action": "wait_confirm", "confirm_label": "I'm logged in"},
            {"id": 4, "instruction": "Press F12 to open DevTools → click the 'Application' tab", "action": "wait_confirm", "confirm_label": "I can see the Application tab"},
            {"id": 5, "instruction": "In the left panel: Storage → Cookies → https://www.douyin.com", "action": "wait_confirm", "confirm_label": "I can see the cookie list"},
            {"id": 6, "instruction": "Find the row named 'sessionid' and double-click its Value to copy it", "action": "wait_confirm", "confirm_label": "Copied!"},
            {"id": 7, "instruction": "Paste your Douyin sessionid here:", "action": "text_input", "field": "douyin_cookie", "placeholder": "Paste sessionid value...", "validate": "test_douyin_cookie"},
        ]
    },
    "tiktok_cookie": {
        "title": "TikTok Cookie Setup (Advanced — at your own risk)",
        "description": "Fallback for scouting TikTok when you don't have a TikHub API key. Uses your logged-in TikTok sessionid, which means TikTok sees the scout requests as coming from your account. This is against TikTok's Terms of Service and may result in your account being throttled, shadowbanned, or suspended. The recommended path is the TikHub API — get a free-tier key at tikhub.io.",
        "risk_level": "high",
        "steps": [
            {"id": 1, "instruction": "Read this carefully: using your TikTok sessionid for scouting violates TikTok's Terms of Service. ViralMint is not affiliated with TikTok and the maintainers are not responsible for any consequences to your account. Use the TikHub API path instead unless you have specifically accepted this risk.", "action": "wait_confirm", "confirm_label": "I understand and accept the risk"},
            {"id": 2, "instruction": "Open Chrome and go to TikTok", "action": "open_url", "url": "https://www.tiktok.com"},
            {"id": 3, "instruction": "Log in to your TikTok account", "action": "wait_confirm", "confirm_label": "I'm logged in"},
            {"id": 4, "instruction": "Press F12 → Application → Cookies → www.tiktok.com", "action": "wait_confirm", "confirm_label": "I see the cookies"},
            {"id": 5, "instruction": "Find 'sessionid' and copy its full value", "action": "wait_confirm", "confirm_label": "Copied!"},
            {"id": 6, "instruction": "Paste your TikTok sessionid here:", "action": "text_input", "field": "tiktok_cookie", "placeholder": "Paste sessionid value...", "validate": "test_tiktok_cookie"},
        ]
    },
    "youtube_auth": {
        "title": "YouTube Upload Authorization",
        "description": "Connect your YouTube channel so ViralMint can upload videos",
        "steps": [
            {"id": 1, "instruction": "Click below to open Google's sign-in page and authorize ViralMint", "action": "oauth_button", "endpoint": "/api/settings/youtube-auth", "button_label": "Connect YouTube Account"},
            {"id": 2, "instruction": "Sign in with the Google account that owns your YouTube channel", "action": "wait_oauth", "timeout_seconds": 120},
            {"id": 3, "instruction": "Grant ViralMint permission to upload videos on your behalf", "action": "wait_oauth", "timeout_seconds": 120},
            {"id": 4, "instruction": "Connected! ViralMint can now upload to your YouTube channel.", "action": "success"},
        ]
    },
    "tiktok_upload_auth": {
        "title": "TikTok Upload Authorization",
        "description": "Connect your TikTok account to enable direct video publishing",
        "steps": [
            {"id": 1, "instruction": "Click below to authorize ViralMint via TikTok's official API", "action": "oauth_button", "endpoint": "/api/settings/tiktok-upload-auth", "button_label": "Connect TikTok Account"},
            {"id": 2, "instruction": "Log in to TikTok and approve the video upload permission", "action": "wait_oauth", "timeout_seconds": 120},
            {"id": 3, "instruction": "All done! ViralMint can now post to your TikTok.", "action": "success"},
        ]
    },
    "telegram": {
        "title": "Connect Telegram",
        "description": "Get ViralMint notifications and chat with agents via Telegram",
        "steps": [
            {"id": 1, "instruction": "Open Telegram and search for @BotFather", "action": "open_url", "url": "https://t.me/BotFather"},
            {"id": 2, "instruction": "Send /newbot → choose a name (e.g. 'ViralMint Notify') → choose a username ending in 'bot'", "action": "wait_confirm", "confirm_label": "I have my bot token"},
            {"id": 3, "instruction": "Paste your bot token (looks like 123456789:ABCdef...):", "action": "text_input", "field": "telegram_bot_token", "placeholder": "123456789:ABCdef...", "validate": "test_telegram_token"},
            {"id": 4, "instruction": "Token saved! Now open your new bot in Telegram and send /start to activate notifications.", "action": "open_url_dynamic", "url_field": "telegram_bot_url"},
            {"id": 5, "instruction": "Waiting for you to send /start to your bot...", "action": "wait_ws_event", "event": "telegram_connected"},
        ]
    },
}

# Maps from what the Planner needs → which wizard to show
SETUP_NEEDS = {
    "douyin_scout":         "douyin_cookie",
    "tiktok_scout":         "tiktok_cookie",
    "youtube_upload":       "youtube_auth",
    "tiktok_upload":        "tiktok_upload_auth",
    "telegram_notifications": "telegram",
}
