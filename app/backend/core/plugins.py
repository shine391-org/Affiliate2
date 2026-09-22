# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Plugin registry — extension seam for proprietary overlays.

OSS exposes registration helpers; downstream packages (e.g. the desktop
installer's proprietary overlay) call them at import time to plug in extra
FastAPI routers, planner actions, and config keys without touching OSS code.

Contract: see docs/OVERLAY.md.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

_routers: list[APIRouter] = []
_planner_actions: dict[str, Callable[..., Any]] = {}
_config_keys: dict[str, Any] = {}


def register_router(router: APIRouter) -> None:
    """Register a FastAPI router. Mounted under /api by main.create_app()."""
    _routers.append(router)


def register_planner_action(name: str, handler: Callable[..., Any]) -> None:
    """Register a chat-planner action handler. Name must be unique."""
    if name in _planner_actions:
        raise ValueError(f"planner action {name!r} already registered")
    _planner_actions[name] = handler


def register_config_key(key: str, value: Any) -> None:
    """Register a /api/config/{key} value."""
    _config_keys[key] = value


def get_routers() -> list[APIRouter]:
    return list(_routers)


def get_planner_action(name: str) -> Callable[..., Any] | None:
    return _planner_actions.get(name)


def get_config_key(key: str) -> Any:
    return _config_keys.get(key)


def load_overlay() -> str | None:
    """Import the configured overlay package (side-effect: registers plugins).

    Reads VIRALMINT_OVERLAY env var (default: 'viralmint_overlay'). Returns the
    imported module name, or None if no overlay is installed. Silent on
    ImportError so OSS works standalone.
    """
    import importlib
    import logging
    import os

    name = os.getenv("VIRALMINT_OVERLAY", "viralmint_overlay")
    if not name:
        return None
    try:
        importlib.import_module(name)
        return name
    except ImportError:
        return None
    except Exception as e:
        logging.getLogger(__name__).warning(f"overlay {name!r} failed to load: {e}")
        return None
