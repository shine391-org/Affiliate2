# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Persistent device identification for cloud sync.
Generates a UUID on first run and persists it to storage/.device_id.
"""
from pathlib import Path
from uuid import uuid4

from backend.config import settings

_device_id: str | None = None


def get_device_id() -> str:
    """Return a persistent device ID, creating one if it doesn't exist."""
    global _device_id
    if _device_id:
        return _device_id

    id_path = settings.STORAGE_ROOT / ".device_id"
    id_path.parent.mkdir(parents=True, exist_ok=True)

    if id_path.exists():
        _device_id = id_path.read_text().strip()
        if _device_id:
            return _device_id

    _device_id = str(uuid4())
    id_path.write_text(_device_id)
    return _device_id
