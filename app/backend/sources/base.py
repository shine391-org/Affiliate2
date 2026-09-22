"""Provider contracts for real platform discovery.

Providers own platform access and normalization into the Scout row shape.
They do NOT own UI, persistence, jobs, Radar ranking, or cross-platform fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ProviderStatus(str, Enum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ERROR = "ERROR"


@dataclass(slots=True)
class SourceProviderResult:
    platform: str
    query: str
    status: ProviderStatus
    items: list[dict] = field(default_factory=list)
    error: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        # A provider result may never claim another platform's rows.
        for item in self.items:
            item_platform = item.get("platform")
            if item_platform != self.platform:
                raise ValueError(
                    f"Provider {self.platform!r} returned row for {item_platform!r}"
                )


class SourceProviderError(RuntimeError):
    """Raised at the Scout boundary when a native provider cannot serve the request."""

    def __init__(self, platform: str, status: ProviderStatus, message: str):
        self.platform = platform
        self.status = status
        self.message = message
        super().__init__(f"{platform} [{status.value}]: {message}")


class SourceProvider(Protocol):
    platform: str
    name: str

    async def search(self, query: str, limit: int = 15) -> SourceProviderResult:
        ...
