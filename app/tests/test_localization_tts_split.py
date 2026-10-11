# SPDX-License-Identifier: AGPL-3.0-only
"""The TTS/C7 split must not disable a patch seam or lose a guard.

Three distinct shapes have broken across the two file splits, and all three
fail GREEN, which is why they are pinned explicitly rather than left to the
behavioural suites to notice:

1. A **function** patched on ``localization_service`` no longer reaching the
   moved call site, because the moved module bound the original object.
2. A **scalar** patched on the facade (a deadline, a batch size) having no
   effect, because the moved module holds its own copy of the constant.
3. A **pass-by-reference** use - the name handed to ``asyncio.to_thread`` as a
   value rather than called - keeping the real implementation inside the
   worker thread even though the call-site rewrite looked correct.

Case 3 is the one that hides best: rewriting ``_f(x)`` to ``_facade._f(x)``
leaves ``asyncio.to_thread(_f, x)`` untouched, so the seam appears wired and
the injected double is still bypassed.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from backend.services import localization_service as svc
from backend.services import localization_tts as tts
from backend.services import localization_translation as tr


def _module_tree() -> ast.Module:
    path = pathlib.Path("app/backend/services/localization_tts.py")
    return ast.parse(path.read_text(encoding="utf-8"))


def _patched_facade_names() -> set[str]:
    """Every name any test installs on the facade, computed not guessed."""
    aliases = {"ls", "svc", "service", "lsr", "localization_service"}
    found: set[str] = set()
    for path in pathlib.Path("app/tests").glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setattr"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in aliases
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                found.add(node.args[1].value)
    return found


# ── shape 1 & 2: nothing patchable is bound by value ─────────────────────


def test_no_patched_tts_name_is_imported_by_value():
    """A patched name must not appear as a bare module-level binding.

    This is the structural guard that catches a revert to `from ... import
    name`, and it is the one that actually bites: a runtime "is the patch
    visible" check passes even on a broken seam, because an unpatched original
    and the proxy return the same object.
    """
    tree = _module_tree()
    bound = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "backend.services.localization_service"
        for alias in node.names
    }
    offenders = sorted(_patched_facade_names() & bound)
    assert not offenders, (
        f"patched names bound by value in localization_tts: {offenders}. A test "
        f"double installed on localization_service will not reach them."
    )


def test_the_tts_facade_import_is_the_last_statement():
    """Import order, not style, is what keeps the split importable.

    ``localization_tts`` imports its collaborators back from
    ``localization_service``, so the facade's own import of it must run after
    every collaborator is defined. Checked as "the last statement", not "the
    only import", because the C6 facade import legitimately precedes it.
    """
    tree = ast.parse(
        pathlib.Path("app/backend/services/localization_service.py")
        .read_text(encoding="utf-8")
    )
    tts_line = [
        n.lineno for n in tree.body
        if isinstance(n, ast.ImportFrom)
        and n.module == "backend.services.localization_tts"
    ]
    assert tts_line, "no facade import of localization_tts found"
    body = [n for n in tree.body if not isinstance(n, ast.Expr)]
    assert body[-1].lineno == tts_line[0], (
        f"facade import at line {tts_line[0]} is not the last statement "
        f"(last is {body[-1].lineno})"
    )


def test_public_names_are_the_same_objects_as_before_the_split():
    """Re-export, not a copy."""
    for name in (
        "_tts_config", "_generate_tts_chunk", "_audio_duration_ms",
        "validate_localization_tts_preflight", "_default_speech_voice",
        "_trim_edge_tts_boundary_silence_sync", "_concat_tts_parts_sync",
        "_extract_reference_clip_sync", "_fit_cadence_chunk_sync",
    ):
        assert getattr(svc, name) is getattr(tts, name), (
            f"{name} differs between the facade and the implementation"
        )


# ── shape 3: pass-by-reference reaches the facade ────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "_trim_edge_tts_boundary_silence_sync",
        "_concat_tts_parts_sync",
        "_extract_reference_clip_sync",
    ],
)
def test_callable_passed_to_a_thread_is_resolved_through_the_facade(name):
    """`asyncio.to_thread(_f, ...)` is not an `ast.Call` and is easy to miss.

    Rewriting the CALL SITES is not enough: a name handed over as a value runs
    inside the worker without any dispatch, so a patched double is bypassed
    while the file still reads as if the seam were wired.
    """
    tree = _module_tree()
    bare: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == name
        ):
            bare.append(node.lineno)
    assert not bare, (
        f"{name} is referenced as a bare value at line(s) {bare}. Passed to "
        f"asyncio.to_thread that bypasses the facade entirely, so an "
        f"injected double is ignored while the suite stays green."
    )


# ── shape 2, behaviourally ───────────────────────────────────────────────


def test_scalar_bounds_resolve_through_the_facade(monkeypatch):
    """A patched deadline or batch size must be what the moved code reads."""
    monkeypatch.setattr(svc, "_TTS_CHUNK_DEADLINE_S", 0.02)
    monkeypatch.setattr(svc, "_TTS_REQUEST_TIMEOUT_S", 0.03)
    assert tts._tts_chunk_deadline_s() == 0.02
    assert tts._tts_request_timeout_s() == 0.03

    monkeypatch.setattr(svc, "_TRANSLATION_BATCH_SIZE", 1)
    assert tr._translation_batch_size() == 1


@pytest.mark.asyncio
async def test_patched_helper_resolves_through_the_facade_at_call_time(monkeypatch):
    """A patch on the facade must be what the moved code dispatches to.

    The contract is about the CALL SITE, not about the moved module's own
    attribute: patching ``svc._generate_tts_chunk`` changes what
    ``_facade._generate_tts_chunk`` resolves to, and every internal call site
    is written to go through that. Asserting the moved module's own name would
    test the wrong thing - it is the implementation, and a test double is
    never installed over it.
    """
    async def fake(*_a, **_k):
        return 4242

    monkeypatch.setattr(svc, "_generate_tts_chunk", fake)
    assert tts._facade._generate_tts_chunk is fake
    assert await tts._facade._generate_tts_chunk("x") == 4242

    async def fake_measure(*_a, **_k):
        return 77

    monkeypatch.setattr(svc, "_audio_duration_ms", fake_measure)
    assert await tts._facade._audio_duration_ms("x") == 77


@pytest.mark.parametrize(
    "name",
    [
        "_audio_duration_ms",
        "_generate_tts_chunk",
        "_tts_config",
        "_trim_edge_tts_boundary_silence_sync",
        "_concat_tts_parts_sync",
        "_extract_reference_clip_sync",
        "_fit_cadence_chunk_sync",
    ],
)
def test_no_internal_call_site_bypasses_the_facade(name):
    """Every use inside the moved module goes through `_facade`.

    A bare call or a bare pass-by-reference would run the real implementation
    while a test double is installed on localization_service - the seam reads
    as wired and the injected behaviour never happens.
    """
    tree = _module_tree()
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == name
    ]
    assert not offenders, (
        f"{name} is used as a bare name at line(s) {offenders} inside "
        f"localization_tts; it must be reached as _facade.{name}(...) so a "
        f"patch on localization_service applies"
    )