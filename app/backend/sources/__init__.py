"""App-native source-provider boundary for Affiliate2 discovery."""

from .base import ProviderStatus, SourceProviderError, SourceProviderResult
from .registry import (
    fetch_creator_history,
    get_source_provider,
    has_source_provider,
    search_source_provider,
)

__all__ = [
    "ProviderStatus",
    "SourceProviderError",
    "SourceProviderResult",
    "fetch_creator_history",
    "get_source_provider",
    "has_source_provider",
    "search_source_provider",
]
