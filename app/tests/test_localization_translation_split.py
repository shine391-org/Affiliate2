# SPDX-License-Identifier: AGPL-3.0-only
"""The C6 split must not silently disable the monkeypatch seam.

Splitting the C6 stage into ``localization_translation`` moved real logic out
of ``localization_service`` while every caller - including tests and donor
integrations - still patches collaborators as ATTRIBUTES of
``localization_service``.

A plain ``from ... import name`` in the moved module binds the ORIGINAL object
into its own namespace, so the patch on the facade never reaches the call
site. Nothing raises; the stage just quietly stops honouring the seam and the
suite reports behaviour that no longer matches the injected double.

This failure is invisible to a test that only checks the happy path, which is
why the seam is pinned here directly: each case patches the facade and asserts
the value the MOVED code actually observes.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from backend.services import localization_service as svc
from backend.services import localization_translation as tr


# ── the seam itself ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "_profile_ai_route_clients_compat",
        "_profile_ai_route_specs",
        "_translation_guard_checks",
        "_translation_candidates",
        "_translation_prompt",
        "_append_ai_failure",
        "_extract_reference_clip_sync",
        "_AIStageRouteState",
    ],
)
def test_patching_the_facade_is_visible_inside_the_moved_module(name, monkeypatch):
    """A facade patch must be what the moved code sees, not the original."""
    sentinel = object()
    monkeypatch.setattr(svc, name, lambda *a, **k: sentinel)
    assert getattr(tr, name)() is sentinel, (
        f"{name} is bound in localization_translation's own namespace, so a "
        f"patch on localization_service cannot reach it. The call site keeps "
        f"the ORIGINAL object and the seam fails green."
    )


@pytest.mark.parametrize(
    "name",
    [
        "_profile_ai_route_clients_compat",
        "_profile_ai_route_specs",
        "_translation_guard_checks",
        "_translation_candidates",
        "_translation_prompt",
        "_append_ai_failure",
        "_extract_reference_clip_sync",
    ],
)
def test_the_moved_module_has_no_direct_import_of_a_patched_collaborator(name):
    """Structural guard, and the one that actually catches the mutation.

    Asserting only "a patch is visible" passes even when the proxy body is
    rewritten to call the original - both return the same object when nothing
    is patched, so the runtime test stays green on a broken seam. This
    assertion pins the SHAPE instead: a plain `from localization_service import
    <name>` inside the moved module is the defect itself, and it is visible in
    the source without needing a patch to be installed.
    """
    path = pathlib.Path("app/backend/services/localization_translation.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound_directly = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "backend.services.localization_service"
        for alias in node.names
    }
    assert name not in bound_directly, (
        f"{name} is imported directly into localization_translation. A test "
        f"double installed on localization_service will not reach the call "
        f"site; the stage quietly uses the real implementation instead."
    )


@pytest.mark.asyncio
async def test_patching_the_async_chat_call_is_visible_inside_the_moved_module(
    monkeypatch,
):
    """Same seam, async collaborator.

    ``_call_ai_chat_resilient`` is owned by the moved module but DISPATCHED
    through the facade, so a fake installed on ``localization_service`` must be
    what the batch loop actually awaits. Asserting on the batch loop rather
    than on the moved module's own name is deliberate: the public name is the
    implementation, and the seam that matters is the one a test double has to
    intercept.

    The fake must return a shape the batch loop accepts, otherwise the loop
    fails before the assertion and the test proves nothing about dispatch.
    """
    seen: list = []

    async def fake(*args, **kwargs):
        seen.append(args)
        prompt = kwargs.get("messages") or []
        sid = "s1"
        for message in prompt:
            content = message.get("content", "")
            if '"id"' in content:
                import re as _re
                found = _re.search(r'"id"\s*:\s*"([^"]+)"', content)
                if found:
                    sid = found.group(1)
                    break
        return json.dumps([{"id": sid, "text": "Xin chao"}], ensure_ascii=False)

    monkeypatch.setattr(svc, "_call_ai_chat_resilient", fake)

    from backend.core.localization import LocalizationProfile, LocalizationSegment

    profile = LocalizationProfile(
        id="qa", name="QA", target_aspect="source", alignment_mode="off",
        diarization_mode="off", video_retime_mode="off",
        translation_retry_attempts=1,
        translation_attempt_timeout_s=10,
        translation_total_timeout_s=20,
    )
    batch = [LocalizationSegment(
        id="s1", source_text="hello", source_start_ms=0, source_end_ms=500,
    )]

    texts, failed = await tr._translate_batch_resilient(
        object(), batch, target_language="Vietnamese",
        context="ctx", profile=profile, for_dub=True,
    )
    assert not failed, f"the batch unexpectedly failed: {failed}"
    assert seen, (
        "the batch loop never reached the patched _call_ai_chat_resilient on "
        "localization_service; it called the moved module's own binding, so "
        "the test double was bypassed while the suite stayed green"
    )


def test_mutable_recheck_state_resolves_through_the_facade(monkeypatch):
    """Mutable module state is the same class of defect as a patched function.

    The in-flight registry is reset and awaited by tests as
    ``svc._AUTO_RECHECK_TASKS``. A private set inside the moved module would
    make that patch write to a different object than the code reads: the test
    would see an empty set while the real registry stayed populated, and every
    subsequent call would take the ``dependency_busy`` branch.
    """
    replacement = set()
    monkeypatch.setattr(svc, "_AUTO_RECHECK_TASKS", replacement)
    assert tr._auto_recheck_tasks() is replacement


def test_recheck_timeout_bound_resolves_through_the_facade(monkeypatch):
    """A scalar bound is patched to force the timeout branch in tests."""
    monkeypatch.setattr(svc, "_AUTO_RECHECK_TIMEOUT_S", 0.125)
    assert tr._auto_recheck_timeout_s() == 0.125


def test_public_names_are_the_same_objects_as_before_the_split():
    """Re-export, not a copy.

    ``tool_runners`` imports ``translate_transcript`` from the facade, so the
    facade attribute must BE the implementation rather than a look-alike that
    only happens to behave the same today.
    """
    for name in (
        "translate_transcript",
        "_translate_batch_resilient",
        "_recheck_pending_sources",
        "_call_ai_chat_resilient",
        "_emit_ai_progress",
        "_safe_ai_error_text",
        "_attach_ai_failure_context",
        "_TranslationDispatchBudget",
    ):
        assert getattr(svc, name) is getattr(tr, name), (
            f"{name} differs between the facade and the implementation"
        )


# ── structure ───────────────────────────────────────────────────────────


def test_the_facade_imports_run_after_every_collaborator_is_defined():
    """Import order, not convention, is what makes the split importable.

    ``localization_translation`` and ``localization_tts`` both import their
    collaborators back from ``localization_service``, so the facade's own
    imports of them must run after every one of those collaborators is
    defined. Anywhere earlier and a collaborator declared further down is
    simply missing at that moment - an ImportError on a name that exists in
    the file.

    Checked as "both facade imports come after every module-level definition",
    not "is the last statement": after a second split the file has two
    facade imports and only the later one is last.
    """
    path = pathlib.Path("app/backend/services/localization_service.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    facade_lines = [
        n.lineno for n in tree.body
        if isinstance(n, ast.ImportFrom)
        and n.module in (
            "backend.services.localization_translation",
            "backend.services.localization_tts",
        )
    ]
    assert len(facade_lines) == 2, (
        f"expected both facade imports, found {len(facade_lines)}"
    )

    first_facade = min(facade_lines)
    last_def = max(
        n.lineno for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                          ast.Assign, ast.AnnAssign, ast.For, ast.While,
                          ast.If, ast.With, ast.Try))
        and not isinstance(n, ast.Expr)
    )
    assert last_def < first_facade, (
        f"a module-level definition at line {last_def} sits between the "
        f"facade imports starting at {first_facade}; the moved modules would "
        f"import this one before it exists"
    )


def test_the_moved_stage_owns_its_helpers_and_the_facade_keeps_the_rest():
    """The split is a responsibility boundary, not a half-move.

    Guards against 'the facade re-exports everything', which would leave the
    boundary meaningless, and against 'the moved module silently dropped a
    helper the rest of the service still needs'.
    """
    moved = {
        "translate_transcript", "_translate_batch_resilient",
        "_call_ai_chat_resilient", "_recheck_pending_sources",
        "_TranslationDispatchBudget",
    }
    stayed = {
        "materialize_localization_profile", "resolve_localization_profile",
        "synthesize_timed_dub", "_tts_config", "mix_dub_audio",
    }
    for name in moved:
        assert hasattr(tr, name), f"{name} should live in the moved module"
        assert hasattr(svc, name), f"{name} must stay importable from the facade"
    for name in stayed:
        assert not hasattr(tr, name), (
            f"{name} belongs to the dubbing/profile half and must not have "
            f"been dragged into the C6 module"
        )