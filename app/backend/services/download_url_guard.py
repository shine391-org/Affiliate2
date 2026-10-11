# SPDX-License-Identifier: AGPL-3.0-only
"""Fail-closed URL preflight for user/agent-controlled video download targets.

Validates the supplied URL and every explicitly resolved source URL before
yt-dlp/CDP sees it. Network-layer egress ACLs remain the authority for HTTP
redirects and DNS rebinding; URL validation alone is not a socket firewall.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def validate_download_url(url: str) -> str:
    """Reject non-HTTP, ambiguous and non-public download URL targets."""
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("Download URL must be a nonempty, trimmed string")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Download URL has invalid host or port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc or not host
        or parsed.username is not None or parsed.password is not None
        or "%" in host or "\\" in url or any(ord(ch) < 32 for ch in url)
    ):
        raise ValueError("Download URL must be an unambiguous HTTP(S) URL")
    if port not in {None, 80, 443}:
        raise ValueError("Download URL uses an unsupported network port")
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
    ):
        raise ValueError("Download URL cannot target a local/internal host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.startswith(".") or ".." in host:
            raise ValueError("Download URL must have a public hostname") from None
    else:
        if not ip.is_global or ip.is_multicast:
            raise ValueError("Download URL cannot target a private/reserved IP")
    return url


def validate_public_download_target(url: str) -> str:
    """Resolve all DNS answers; reject if ANY route resolves non-publicly."""
    validate_download_url(url)
    host = urlsplit(url).hostname
    assert host is not None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, ValueError) as exc:
            raise ValueError("Download host DNS could not be verified") from exc
        if not answers:
            raise ValueError("Download host has no DNS answers")
        for answer in answers:
            try:
                ip = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
            except (ValueError, IndexError, TypeError) as exc:
                raise ValueError("Download host has invalid DNS response") from exc
            if not ip.is_global or ip.is_multicast:
                raise ValueError("Download host resolves to a private/reserved IP")
    return url
