"""Transitional import bridge from legacy Radar CLI into canonical app code."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = PROJECT_ROOT / "app"


def ensure_canonical_app_imports() -> None:
    value = str(APP_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)
