"""Exercise the runner's real nested progress handlers without media I/O."""
import ast
from pathlib import Path

import pytest

from backend.core import tool_runners


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["_translation_progress", "_rewrite_progress"])
async def test_provider_wait_is_visible_and_not_counted_as_dispatch(handler):
    tree = ast.parse(Path(tool_runners.__file__).read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == handler)
    namespace = {"job_id": "qa", "user_id": "local",
        "translation_runtime": {"attempts": 0, "errors": 0, "fallback_provider_used": False,
                                "current_batch": 1, "total_batches": 1, "http_dispatch_count": 0},
        "translation_batch": {"current": 1, "total": 1}}
    messages = []

    async def progress(*args, **kwargs):
        messages.append(args[2])

    namespace["_tool_progress"] = progress
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "runner-progress", "exec"), namespace)
    await namespace[handler]({"event": "provider_wait", "wait_s": 30,
                              "reason": "cooldown", "dispatch_count": 0})
    assert messages and "provider" in messages[-1].lower() and "30" in messages[-1]
    assert namespace["translation_runtime"]["http_dispatch_count"] == 0
