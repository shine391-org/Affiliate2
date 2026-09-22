"""Registry for app-native source providers."""
from __future__ import annotations

from .base import SourceProvider, SourceProviderResult


def get_source_provider(platform: str) -> SourceProvider | None:
    platform = str(platform or "").strip().lower()
    if platform == "bilibili":
        from .bilibili import BilibiliProvider

        return BilibiliProvider()
    return None


def has_source_provider(platform: str) -> bool:
    return get_source_provider(platform) is not None


async def search_source_provider(
    platform: str,
    query: str,
    *,
    limit: int = 15,
) -> SourceProviderResult:
    provider = get_source_provider(platform)
    if provider is None:
        raise ValueError(f"No app-native source provider registered for {platform!r}")
    return await provider.search(query, limit=limit)
