# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""YouTube Data API v3 upload service."""
import asyncio
import json
import logging
from pathlib import Path

from backend.config import settings
from backend.core.exceptions import UploadError, UploadAuthError

logger = logging.getLogger(__name__)


async def upload_to_youtube(
    video_path: str,
    title: str,
    description: str = "",
    tags: list[str] = None,
    category_id: str = "22",
    privacy: str = "public",
    credentials_json: str = "",
) -> dict:
    """
    Upload a video to YouTube using OAuth credentials.
    Returns {"video_id": "...", "url": "https://youtube.com/watch?v=..."}.
    """
    if not credentials_json:
        raise UploadAuthError("YouTube OAuth credentials not configured. Connect your YouTube account in Settings.")

    path = Path(video_path)
    if not path.exists():
        raise UploadError(f"Video file not found: {video_path}")

    def _upload():
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds_data = json.loads(credentials_json)
        creds = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
        )

        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": (tags or [])[:15],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10MB chunks
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"YouTube upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        if not video_id:
            raise UploadError("YouTube upload completed but no video ID returned")

        return {
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}",
        }

    try:
        return await asyncio.to_thread(_upload)
    except UploadAuthError:
        raise
    except UploadError:
        raise
    except Exception as e:
        if "invalid_grant" in str(e).lower() or "token" in str(e).lower():
            raise UploadAuthError(f"YouTube OAuth token expired or invalid. Re-connect in Settings. ({e})")
        raise UploadError(f"YouTube upload failed: {e}")


def build_youtube_auth_url() -> str:
    """Build the YouTube OAuth authorization URL."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.YOUTUBE_CLIENT_ID,
                "client_secret": settings.YOUTUBE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.YOUTUBE_REDIRECT_URI],
            }
        },
        scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"],
    )
    flow.redirect_uri = settings.YOUTUBE_REDIRECT_URI

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


async def exchange_youtube_code(code: str) -> dict:
    """Exchange an OAuth authorization code for credentials."""
    from google_auth_oauthlib.flow import Flow

    def _exchange():
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.YOUTUBE_CLIENT_ID,
                    "client_secret": settings.YOUTUBE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [settings.YOUTUBE_REDIRECT_URI],
                }
            },
            scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"],
        )
        flow.redirect_uri = settings.YOUTUBE_REDIRECT_URI
        flow.fetch_token(code=code)

        creds = flow.credentials
        creds_dict = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
        }

        # Get channel info
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", credentials=creds)
        channels = youtube.channels().list(part="snippet", mine=True).execute()
        channel_title = ""
        if channels.get("items"):
            channel_title = channels["items"][0]["snippet"]["title"]

        return {"credentials": creds_dict, "channel_title": channel_title}

    return await asyncio.to_thread(_exchange)
