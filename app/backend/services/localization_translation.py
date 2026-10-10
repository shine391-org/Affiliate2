# SPDX-License-Identifier: AGPL-3.0-only
"""C6 translation stage — batching, provider retries and checkpoint resume.

Split out of ``localization_service`` along a responsibility boundary, not a
line quota. This module owns the C6 stage end to end: dispatch budgeting,
resilient provider calls under a shared cooldown, batch translation, the
bounded re-check of unresolved source windows, and the durable checkpoint
that lets a cancelled or crashed run resume without re-paying for batches
that were already accepted.

``localization_service`` re-exports every name below, so existing
``from backend.services.localization_service import ...`` callers and the
tests that monkeypatch module attributes keep working unchanged. The
provider/exceptions imports stay FUNCTION-LOCAL exactly as they were in the
original module: hoisting them here would make this module depend on the AI
provider layer at import time, which is what those local imports avoided.

Deliberately NOT here: profile resolution/materialization and every
dubbing/TTS concern, both of which still live in ``localization_service``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from pathlib import Path

from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.services import localization_service as _facade
from backend.services.localization_service import (
    _C6_TRANSLATION_PROMPT_VERSION,
    _MAX_AI_FAILURE_HISTORY,
    _TRANSLATION_BATCH_SIZE,
    _await_provider_operation,
    _cancel_checkpoint,
    _selected_ai_connection_id,
    _strip_json_fence,
    _translation_context,
    _validate_translation_payload,
    logger,
)

# These three travelled WITH the C6 cluster when it moved out of
# localization_service: they are the bounds of the bounded source re-check and
# nothing outside this module reads them. They are defined HERE rather than
# imported from the facade, because the facade imports this module - a
# back-reference would be a circular import, and leaving them only in the
# facade would put their definitions back among the stage this module owns.
#
# `_AUTO_RECHECK_TASKS` is NOT simply a private constant: tests reset and await
# it as `svc._AUTO_RECHECK_TASKS`, so it is observable state and must resolve
# through the facade. A module-level set here would leave that patch writing to
# a different object than the one this code reads, and the test would then
# observe an empty set while the real one stayed populated - every later call
# taking the `dependency_busy` branch.
def _auto_recheck_tasks() -> set[asyncio.Task]:
    return _facade._AUTO_RECHECK_TASKS


# Same reason for the deadline bound: tests shorten it to force the timeout
# branch, and a local copy would ignore that patch entirely.
def _auto_recheck_timeout_s() -> float:
    return _facade._AUTO_RECHECK_TIMEOUT_S


_AUTO_RECHECK_MAX_CUES = 3


class _TranslationDispatchBudget:
    """One original-batch budget, shared by transport/repair/routes/split."""

    def __init__(self, limit=4):
        self.limit = limit
        self.count = 0

    def consume(self):
        if self.count >= self.limit:
            from backend.core.ai_provider import AIRequestBudgetError
            raise AIRequestBudgetError(f"translation HTTP dispatch budget exhausted ({self.limit})")
        self.count += 1


async def _emit_ai_progress(progress_cb, payload: dict) -> None:
    if progress_cb is None:
        return
    result = progress_cb(payload)
    if asyncio.iscoroutine(result):
        await result


def _safe_ai_error_text(exc: Exception, client=None, limit: int = 240) -> str:
    text = str(exc)
    key = str(getattr(client, "api_key", "") or "")
    if key and len(key) >= 4:
        text = text.replace(key, "***")
    return text[:limit]


def _attach_ai_failure_context(exc: Exception, stage: str, route_state: _AIStageRouteState | None):
    if route_state is None:
        return exc
    try:
        exc.ai_stage = stage
        exc.ai_failure_chain = list(route_state.failure_chain)
        last_failure_connection = (
            route_state.failure_chain[-1].get("connection_id")
            if route_state.failure_chain else None
        )
        exc.ai_selected_connection = (
            last_failure_connection
            or getattr(route_state.current, "connection_name", None)
        )
        exc.ai_sticky_route_index = route_state.sticky_index
        exc.ai_attempt_count = route_state.attempt_count
        exc.ai_success_count = route_state.success_count
    except Exception:
        pass
    return exc


async def _call_ai_chat_resilient_impl(
    ai,
    fallback_ai,
    messages: list[dict],
    max_tokens: int = 3000,
    max_attempts: int = 3,
    *,
    attempt_timeout_s: float = 75,
    total_timeout_s: float = 180,
    progress_cb=None,
    stage: str = "translation",
    route_state: _AIStageRouteState | None = None,
    deadline_at: float | None = None,
    dispatch_budget: _TranslationDispatchBudget | None = None,
    cancel_check=None,
) -> str:
    """One owner for AI retry/fallback with hard wall-clock budgets.

    SDK retries are disabled by the callers. Each attempt is wrapped in
    asyncio.wait_for so a half-open SSE/proxy transport cannot keep a stage
    stuck forever. A saved fallback provider/model is tried after the primary.
    """
    import random
    from backend.core.ai_provider import (
        AIClient, AIRequestContext, AIProviderCooldownError, AIRequestBudgetError,
        is_transient_ai_error,
    )

    loop = asyncio.get_running_loop()
    local_deadline = loop.time() + float(total_timeout_s)
    deadline = min(local_deadline, deadline_at) if deadline_at is not None else local_deadline
    last_error: Exception | None = None

    if route_state is not None:
        start_index = min(route_state.sticky_index, len(route_state.clients) - 1)
        usable_clients = [
            (
                "primary" if idx == 0 else f"fallback-{idx}",
                route_state.clients[idx],
                idx,
            )
            for idx in range(start_index, len(route_state.clients))
        ]
    else:
        raw_clients = [ai] + ([fallback_ai] if fallback_ai is not None else [])
        usable_clients = [
            ("primary" if idx == 0 else f"fallback-{idx}", client, idx)
            for idx, client in enumerate(raw_clients)
            if client is not None
        ]

    class DispatchObserver:
        # Called by AIClient only AFTER provider admission, immediately before
        # each wire request. Keep the existing shared budget and count the same
        # event, rather than claiming a dispatch while merely waiting in queue.
        count = 0
        def consume(self):
            if dispatch_budget is not None:
                dispatch_budget.consume()
            self.count += 1
            if route_state is not None:
                route_state.attempt_count += 1

    wire = DispatchObserver()
    for client_pos, (role, client, route_index) in enumerate(usable_clients):
        if hasattr(client, "max_retries"):
            client.max_retries = 0

        # Reserve a fair share of the remaining wall-clock budget for every
        # provider still in the chain. Otherwise 3 hung primary attempts can
        # consume the entire global deadline and the configured fallback never
        # gets a single request.
        providers_left = len(usable_clients) - client_pos
        provider_deadline = min(
            deadline,
            loop.time() + max(0.0, (deadline - loop.time()) / max(providers_left, 1)),
        )

        policy = getattr(client, "traffic_policy", None) or {}
        client_attempts = min(max_attempts, 2) if policy.get("enabled") else max_attempts
        for attempt in range(1, client_attempts + 1):
            await _cancel_checkpoint(cancel_check, "translation")
            global_remaining = deadline - loop.time()
            provider_remaining = provider_deadline - loop.time()
            if global_remaining <= 0:
                timeout_error = TimeoutError(
                    f"{stage} AI deadline exceeded after {int(total_timeout_s)}s"
                )
                raise _attach_ai_failure_context(timeout_error, stage, route_state) from last_error
            if provider_remaining <= 0:
                break

            timeout_s = min(
                float(attempt_timeout_s),
                global_remaining,
                provider_remaining,
            )
            await _emit_ai_progress(progress_cb, {
                "event": "attempt",
                "stage": stage,
                "role": role,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "provider": getattr(client, "connection_name", None) or getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
                "model": getattr(client, "model", None),
                "timeout_s": round(timeout_s, 1),
            })
            # Progress/DB callbacks may yield and observe a cancellation after
            # the attempt's initial check. Do not schedule chat before checking
            # again: its task could dispatch while the async check is pending.
            await _cancel_checkpoint(cancel_check, f"{stage} dispatch")
            dispatch_before = wire.count
            try:
                chat_kwargs = {"messages": messages, "max_tokens": max_tokens}
                if isinstance(client, AIClient):
                    # Per-request state: independent jobs cannot overwrite a
                    # shared client's deadline/progress/budget during admission.
                    chat_kwargs["request_context"] = AIRequestContext(
                        deadline_at=min(deadline, provider_deadline),
                        request_timeout_s=timeout_s, progress_cb=progress_cb,
                        dispatch_budget=wire,
                    )
                else:
                    # Compatibility test doubles represent exactly one wire
                    # attempt. Canonical clients count AFTER admission instead.
                    wire.consume()
                result = await _await_provider_operation(
                    client.chat(**chat_kwargs),
                    timeout_s=provider_remaining if policy.get("enabled") else timeout_s,
                    cancel_check=cancel_check,
                )
                if route_state is not None:
                    route_state.sticky_index = route_index
                    route_state.success_count += 1
                return result
            except Exception as exc:
                from backend.core.exceptions import JobCancelledError
                if isinstance(exc, JobCancelledError):
                    raise
                last_error = exc
                failure = {
                    "connection_id": getattr(client, "connection_name", None),
                    "provider": getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
                    "model": getattr(client, "model", None),
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error": _safe_ai_error_text(exc, client),
                    "dispatched": wire.count > dispatch_before,
                }
                if route_state is not None:
                    _append_ai_failure(route_state, failure)
                await _emit_ai_progress(progress_cb, {
                    "event": "error",
                    "stage": stage,
                    "role": role,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "provider": getattr(client, "connection_name", None) or getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
                    "model": getattr(client, "model", None),
                    "error": _safe_ai_error_text(exc, client),
                })
                if isinstance(exc, (AIProviderCooldownError, AIRequestBudgetError)):
                    # Waiting/budget exhaustion is a resumable stop, not a new
                    # transport error to multiply through retries/fallbacks.
                    raise _attach_ai_failure_context(exc, stage, route_state)
                transient = is_transient_ai_error(exc)
                # Non-transient errors do not deserve repeated attempts on the
                # same provider, but a separately configured fallback may still
                # be healthy (e.g. bad/deprecated primary model).
                if not transient:
                    break
                if attempt < client_attempts:
                    remaining = min(
                        deadline - loop.time(),
                        provider_deadline - loop.time(),
                    )
                    if remaining <= 0:
                        break
                    backoff = min(
                        8.0,
                        (1.5 ** (attempt - 1)) + random.uniform(0.1, 0.4),
                        remaining,
                    )
                    await _emit_ai_progress(progress_cb, {
                        "event": "retry_wait",
                        "stage": stage,
                        "role": role,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "backoff_s": round(backoff, 2),
                    })
                    try:
                        await _await_provider_operation(
                            asyncio.sleep(max(0.0, backoff)),
                            timeout_s=max(0.001, remaining),
                            cancel_check=cancel_check,
                        )
                    except TimeoutError:
                        # This provider's share expired while waiting. Let the
                        # route/global deadline owner choose fallback or emit
                        # the established deadline error with provenance.
                        break

        next_pos = client_pos + 1
        if next_pos < len(usable_clients):
            _next_role, next_client, _next_index = usable_clients[next_pos]
            await _emit_ai_progress(progress_cb, {
                "event": "switch_fallback",
                "stage": stage,
                "provider": getattr(next_client, "connection_name", None) or getattr(getattr(next_client, "provider", None), "value", str(getattr(next_client, "provider", ""))),
                "model": getattr(next_client, "model", None),
            })

    if loop.time() >= deadline:
        timeout_error = TimeoutError(
            f"{stage} AI deadline exceeded after {int(total_timeout_s)}s"
        )
        raise _attach_ai_failure_context(timeout_error, stage, route_state) from last_error
    if last_error is not None:
        raise _attach_ai_failure_context(last_error, stage, route_state)
    raise RuntimeError(f"{stage} AI has no usable provider")


async def _translate_batch_resilient(
    ai,
    batch: list[LocalizationSegment],
    *,
    route_state: _AIStageRouteState | None = None,
    target_language: str,
    context: str,
    profile: LocalizationProfile,
    for_dub: bool,
    fallback_ai=None,
    progress_cb=None,
    deadline_at: float | None = None,
    source_language: str | None = None,
    localized_terms: dict[str, str] | None = None,
    accepted_cb=None,
    dispatch_budget: _TranslationDispatchBudget | None = None,
    _split_depth: int = 0,
    _semantic_repair: bool = True,
    _repair_issues: dict | None = None,
    context_segments=None,
    drafts: dict | None = None,
    _semantic_attempt: int = 1,
    cancel_check=None,
) -> tuple[list[str], set[int]]:
    if not batch:
        return [], set()
    if dispatch_budget is None:
        dispatch_budget = _TranslationDispatchBudget()
    if deadline_at is None:
        deadline_at = asyncio.get_running_loop().time() + float(profile.translation_total_timeout_s)
    prompt = _translation_prompt(target_language=target_language, batch=batch,
        transcript_context=context, profile=profile, for_dub=for_dub, localized_terms=localized_terms)
    # Global context may retain only the beginning/end of a long video. Keep
    # immediate source neighbours for middle batches and targeted repairs too.
    rows = list(context_segments or [])
    positions = {s.id: i for i, s in enumerate(rows)}
    selected_ids = {s.id for s in batch}
    neighbours = {}
    for segment in batch:
        position = positions.get(segment.id)
        if position is None:
            continue
        for index in (position - 1, position + 1):
            if 0 <= index < len(rows) and rows[index].id not in selected_ids:
                neighbour = rows[index]
                neighbours[neighbour.id] = {"source_id": neighbour.id,
                    "speaker_id": neighbour.speaker_id, "source_text": neighbour.source_text[:300]}
    if neighbours:
        prompt += "\n\nLOCAL SOURCE CONTEXT (read-only; do not output these IDs):\n" + json.dumps(
            list(neighbours.values()), ensure_ascii=False)
    if _repair_issues:
        prompt += "\n\nTARGETED REPAIR: Only the supplied rejected IDs. Preserve meaning; fix these invariants:\n" + json.dumps(_repair_issues, ensure_ascii=False)
    raw = await _facade._call_ai_chat_resilient(ai, fallback_ai,
        messages=[{"role": "user", "content": prompt}], max_tokens=3000,
        max_attempts=profile.translation_retry_attempts,
        attempt_timeout_s=profile.translation_attempt_timeout_s,
        total_timeout_s=profile.translation_total_timeout_s, progress_cb=progress_cb,
        stage="translation", route_state=route_state, deadline_at=deadline_at,
        dispatch_budget=dispatch_budget, cancel_check=cancel_check)
    try:
        accepted, rejected, terms = _translation_candidates(raw, batch, localized_terms,
            profile, source_language, target_language, context_segments=context_segments)
    except (json.JSONDecodeError, ValueError) as exc:
        # Only malformed STRUCTURE advances routes or permits one split level.
        # Semantic rejection below never resends accepted siblings.
        if route_state is not None:
            current = route_state.current
            _append_ai_failure(route_state, {
                "connection_id": getattr(current, "connection_name", None),
                "provider": getattr(getattr(current, "provider", None), "value", str(getattr(current, "provider", ""))),
                "model": getattr(current, "model", None), "attempt": 1,
                "error_type": "InvalidStructuredOutput", "error": str(exc)[:240],
            })
            # Advance to the next configured route on malformed structure. This
            # applies to the semantic-repair pass too: a repair that lands on a
            # route returning malformed JSON must not strand the segment, since
            # a later configured route can still answer correctly. The recursive
            # call passes _repair_issues along, so this guard re-fires only
            # while routes remain and each route is visited at most once here.
            if route_state.sticky_index + 1 < len(route_state.clients):
                route_state.sticky_index += 1
                next_client = route_state.current
                await _emit_ai_progress(progress_cb, {"event": "switch_fallback", "stage": "translation",
                    "reason": "invalid_structured_output",
                    "provider": getattr(next_client, "connection_name", None),
                    "model": getattr(next_client, "model", None)})
                return await _translate_batch_resilient(ai, batch, route_state=route_state,
                    target_language=target_language, context=context, profile=profile, for_dub=for_dub,
                    fallback_ai=fallback_ai, progress_cb=progress_cb, deadline_at=deadline_at,
                    source_language=source_language, localized_terms=localized_terms, context_segments=context_segments,
                    accepted_cb=accepted_cb, dispatch_budget=dispatch_budget,
                    _split_depth=_split_depth, _semantic_repair=_semantic_repair, drafts=drafts,
                    _semantic_attempt=_semantic_attempt, cancel_check=cancel_check)
        if len(batch) == 1 or _split_depth >= 1 or _repair_issues:
            logger.warning("Translation structure failed for %d item(s): %s", len(batch), exc)
            return [segment.source_text for segment in batch], set(range(len(batch)))
        await _emit_ai_progress(progress_cb, {"event": "shape_split", "stage": "translation",
            "batch_size": len(batch), "error": str(exc)[:180]})
        mid = len(batch) // 2
        left, lf = await _translate_batch_resilient(ai, batch[:mid], route_state=route_state,
            target_language=target_language, context=context, profile=profile, for_dub=for_dub,
            fallback_ai=fallback_ai, progress_cb=progress_cb, deadline_at=deadline_at,
            source_language=source_language, localized_terms=localized_terms, context_segments=context_segments,
            accepted_cb=accepted_cb, dispatch_budget=dispatch_budget,
            _split_depth=_split_depth + 1, _semantic_repair=_semantic_repair, drafts=drafts,
            _semantic_attempt=_semantic_attempt, cancel_check=cancel_check)
        right, rf = await _translate_batch_resilient(ai, batch[mid:], route_state=route_state,
            target_language=target_language, context=context, profile=profile, for_dub=for_dub,
            fallback_ai=fallback_ai, progress_cb=progress_cb, deadline_at=deadline_at,
            source_language=source_language, localized_terms=localized_terms, context_segments=context_segments,
            accepted_cb=accepted_cb, dispatch_budget=dispatch_budget,
            _split_depth=_split_depth + 1, _semantic_repair=_semantic_repair, drafts=drafts,
            _semantic_attempt=_semantic_attempt, cancel_check=cancel_check)
        return left + right, lf | {mid + i for i in rf}

    if localized_terms is not None:
        localized_terms.update(terms)
    if accepted_cb is not None and accepted:
        # Durable BEFORE repair: failure/cancel cannot discard accepted text.
        await _emit_ai_progress(accepted_cb, accepted)
    if rejected and _semantic_repair:
        pending = [segment for segment in batch if segment.id in rejected]
        await _emit_ai_progress(progress_cb, {"event": "semantic_repair", "stage": "translation",
            "pending_ids": [segment.id for segment in pending], "accepted_count": len(accepted)})
        fixed, failed = await _translate_batch_resilient(ai, pending, route_state=route_state,
            target_language=target_language, context=context, profile=profile, for_dub=for_dub,
            fallback_ai=fallback_ai, progress_cb=progress_cb, deadline_at=deadline_at,
            source_language=source_language, localized_terms=localized_terms, context_segments=context_segments, accepted_cb=accepted_cb,
            dispatch_budget=dispatch_budget, _split_depth=_split_depth, _semantic_repair=False,
            _repair_issues=rejected, drafts=drafts, _semantic_attempt=_semantic_attempt + 1,
            cancel_check=cancel_check)
        for index, (segment, text) in enumerate(zip(pending, fixed)):
            if index not in failed:
                accepted[segment.id] = {"id": segment.id, "text": text}
    if (rejected and not _semantic_repair and route_state is not None
            and route_state.sticky_index + 1 < len(route_state.clients)):
        # A well-formed but wrong answer is also a reason to try the configured
        # fallback. Keep accepted siblings durable and share the SAME deadline
        # and dispatch budget; never silently select an unconfigured provider.
        pending = [segment for segment in batch if segment.id in rejected]
        _append_ai_failure(route_state, {
            "connection_id": getattr(route_state.current, "connection_name", None),
            "model": getattr(route_state.current, "model", None),
            "error_type": "SemanticGuardRejected", "pending_ids": [s.id for s in pending],
        })
        route_state.sticky_index += 1
        await _emit_ai_progress(progress_cb, {"event": "switch_fallback", "stage": "translation",
            "reason": "semantic_rejection", "pending_ids": [s.id for s in pending],
            "provider": getattr(route_state.current, "connection_name", None)})
        fixed, failed = await _translate_batch_resilient(ai, pending, route_state=route_state,
            target_language=target_language, context=context, profile=profile, for_dub=for_dub,
            fallback_ai=fallback_ai, progress_cb=progress_cb, deadline_at=deadline_at,
            source_language=source_language, localized_terms=localized_terms,
            context_segments=context_segments, accepted_cb=accepted_cb, dispatch_budget=dispatch_budget,
            _split_depth=_split_depth, _semantic_repair=False, _repair_issues=rejected,
            drafts=drafts, _semantic_attempt=_semantic_attempt + 1, cancel_check=cancel_check)
        for index, (segment, text) in enumerate(zip(pending, fixed)):
            if index not in failed:
                accepted[segment.id] = {"id": segment.id, "text": text}
        # Only the leaf may keep its FINAL rejected candidate as a draft.
        return ([accepted[s.id]["text"] if s.id in accepted else s.source_text for s in batch],
                {i for i, s in enumerate(batch) if s.id not in accepted})
    if drafts is not None and rejected and not _semantic_repair:
        # Only the final semantic attempt owns this receipt. The parent must
        # never restore its pre-repair raw payload after a child returns; an
        # invalid/provider-failed repair has no final rejected candidate.
        # Auto-only: the final unresolved rejection is preserved as an AUDITED
        # DRAFT. The payload already passed structural validation (exact IDs,
        # nonempty unique text), so this is a semantically rejected but
        # structurally valid candidate -- never an empty/duplicate/missing
        # payload, which stays an error. Only exact source/target plus reasons
        # are recorded; the cue/timeline below are the immutable originals.
        payload_by_id = {item["id"]: item
                         for item in json.loads(_strip_json_fence(raw))}
        for segment in batch:
            if segment.id in accepted or segment.id not in rejected:
                continue
            item = payload_by_id.get(segment.id)
            if item is None or not str(item.get("text") or "").strip():
                continue
            issues = rejected[segment.id]
            drafts[segment.id] = {
                "segment_id": segment.id,
                "source_text": segment.source_text,
                "target_text": item["text"],
                "issues": issues,
                "reasons": [issue.get("kind") for issue in issues],
                "attempts": _semantic_attempt,
                "semantic_attempts": _semantic_attempt,
                "repair_attempted": bool(_repair_issues and segment.id in _repair_issues),
                "origin": "semantic_repair" if _repair_issues else "initial_candidate",
                "source_start_ms": segment.source_start_ms,
                "source_end_ms": segment.source_end_ms,
                "speaker_id": segment.speaker_id,
            }
    return ([accepted[segment.id]["text"] if segment.id in accepted else segment.source_text
             for segment in batch], {index for index, segment in enumerate(batch)
                                    if segment.id not in accepted})


async def _recheck_pending_sources(
    out: LocalizationTranscript,
    pending_ids: list[str],
    auto_drafts: dict,
    *,
    source_media: Path,
    profile: LocalizationProfile,
) -> None:
    """Bounded re-ASR of ONLY the unresolved pending source windows.

    This is a bounded HYPOTHESIS, not adjudicated ground truth: the original
    source_text, cue and timeline stay immutable, and the result is recorded
    under the draft audit so a reviewer can weigh it. It never replaces or
    silently rewrites the canonical source, never touches accepted segments,
    and never calls a paid provider -- it reuses the local Whisper path.
    """
    import tempfile
    import shutil
    from backend.services.whisper_service import whisper_service, resolve_whisper_model, WhisperService

    model = resolve_whisper_model(profile.asr_quality, profile.asr_model)
    by_id = {seg.id: seg for seg in out.segments}
    deadline = asyncio.get_running_loop().time() + _auto_recheck_timeout_s()
    pending_ids = list(dict.fromkeys(pending_ids))
    for index, seg_id in enumerate(pending_ids):
        seg = by_id.get(seg_id)
        record = auto_drafts.get(seg_id)
        if seg is None or not isinstance(record, dict):
            continue
        audit = {"source_text": seg.source_text, "source_start_ms": seg.source_start_ms,
                 "source_end_ms": seg.source_end_ms, "model": model,
                 "authority": "unverified_local_recheck"}
        record["source_recheck"] = audit
        if not WhisperService.is_model_cached(profile.asr_quality, model):
            audit["status"] = "model_unavailable"
            continue  # auxiliary evidence must never trigger model downloads
        remaining = deadline - asyncio.get_running_loop().time()
        if index >= _AUTO_RECHECK_MAX_CUES or remaining <= 0:
            audit["status"] = "budget_exhausted"
            continue
        if _auto_recheck_tasks():
            audit["status"] = "dependency_busy"
            continue  # a timed-out native decode is still settling; no fan-out
        work = Path(tempfile.mkdtemp(prefix="recheck-"))
        clip = work / "source.wav"  # segment IDs are never filesystem names
        start_ms = max(0, seg.source_start_ms - 500)
        end_ms = min(seg.source_end_ms + 500, start_ms + 10000)
        audit.update(window_start_ms=start_ms, window_end_ms=end_ms)

        async def decode():
            await asyncio.to_thread(_extract_reference_clip_sync, source_media, clip,
                start_ms=start_ms, end_ms=end_ms)
            if asyncio.get_running_loop().time() >= deadline:
                return None
            # Optional hypothesis must not hold the canonical compute gate:
            # shielding a timed-out native decoder would otherwise stall C7.
            # The one-inflight registry prevents auxiliary inference fan-out.
            return await whisper_service.transcribe(str(clip), language=out.language or "auto",
                quality=profile.asr_quality, model_name=model)

        task = asyncio.create_task(decode())
        _auto_recheck_tasks().add(task)
        def settled(done, directory=work):
            _auto_recheck_tasks().discard(done)
            # Cancellation does not prove a to_thread/native producer stopped.
            # Leave that private scratch directory for retention cleanup.
            if not done.cancelled():
                done.exception()  # consume failures from a detached late task
                shutil.rmtree(directory, ignore_errors=True)
        task.add_done_callback(settled)
        try:
            tx = await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except asyncio.TimeoutError:
            audit["status"] = "timeout"
            continue
        except Exception as exc:
            audit.update(status="unavailable", error_type=type(exc).__name__)
            continue
        hypothesis = " ".join(str(item.get("text") or "").strip()
            for item in ((tx or {}).get("segments") or []) if isinstance(item, dict)).strip()
        audit["status"] = "hypothesis" if hypothesis else "empty"
        if hypothesis:
            audit["hypothesis_text"] = hypothesis
            audit["executor"] = dict((tx or {}).get("asr_provenance") or {})


async def translate_transcript(
    transcript: LocalizationTranscript,
    *,
    target_language: str,
    profile: LocalizationProfile,
    user_settings,
    for_dub: bool,
    checkpoint_dir: Path | None = None,
    progress_cb=None,
    source_media: Path | None = None,
    cancel_check=None,
) -> LocalizationTranscript:
    import hashlib
    policy = profile.translation_automation
    auto_policy = policy == "auto"
    auto_drafts: dict[str, dict] = {}
    route_clients = _profile_ai_route_clients_compat(
        user_settings, profile, "translation"
    )
    route_state = _AIStageRouteState(route_clients)
    ai = route_clients[0]
    fallback_ai = route_clients[1] if len(route_clients) > 1 else None

    out = transcript.model_copy(deep=True)
    context = _translation_context(transcript)
    failed_total = 0

    checkpoint_file = (checkpoint_dir / "checkpoint.json") if checkpoint_dir else None
    semantic_sig = hashlib.sha256(
        json.dumps([{"id": s.id, "text": s.source_text, "start": s.source_start_ms,
                     "end": s.source_end_ms, "speaker": s.speaker_id, "words": s.source_words}
                    for s in transcript.segments], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    glossary_str = json.dumps(getattr(profile, "glossary", {}) or {}, sort_keys=True, ensure_ascii=False)
    glossary_hash = hashlib.sha256(glossary_str.encode("utf-8")).hexdigest()[:16]
    route_specs = _profile_ai_route_specs(user_settings, profile, "translation")
    route_fingerprint = [
        {
            "connection_id": getattr(client, "connection_name", None),
            "provider": getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
            "model": getattr(client, "model", ""),
            "base_url_override": str(
                getattr(client, "base_url", None)
                or (route_specs[index].get("base_url") if index < len(route_specs) else "") or ""
            ).rstrip("/"),
        }
        for index, client in enumerate(route_clients)
    ]
    checkpoint_meta = {
        "meaning_guard_version": 13,
        "translation_prompt_version": _C6_TRANSLATION_PROMPT_VERSION,
        "source_language": transcript.language,
        "protected_terms": profile.protected_terms,
        "semantic_hash": semantic_sig,
        "target_language": target_language,
        "for_dub": for_dub,
        "speaker_genders": profile.speaker_genders,
        "routes": route_fingerprint,
        "preset": profile.translation_preset,
        "instructions": (getattr(profile, "translation_instructions", "") or "").strip(),
        "guardrails": (getattr(profile, "translation_guardrails", "") or "").strip(),
        "glossary_hash": glossary_hash,
        "translation_policy": policy,
    }
    completed_batches: dict[str, dict] = {}
    localized_terms: dict[str, str] = {}
    saved_drafts: dict[str, dict] = {}
    if checkpoint_file and checkpoint_file.exists():
        try:
            cp_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            if all(cp_data.get(k) == v for k, v in checkpoint_meta.items()):
                saved_batches = cp_data.get("batches", {})
                completed_batches = saved_batches if isinstance(saved_batches, dict) else {}
                if auto_policy:
                    raw_drafts = cp_data.get("auto_drafts")
                    if isinstance(raw_drafts, dict):
                        saved_drafts = {str(k): v for k, v in raw_drafts.items()
                                        if isinstance(v, dict)}
                saved_route_state = cp_data.get("route_state")
                if isinstance(saved_route_state, dict):
                    route_state.sticky_index = min(
                        max(int(saved_route_state.get("sticky_index", 0) or 0), 0),
                        len(route_clients) - 1,
                    )
                    saved_failures = saved_route_state.get("failure_chain") or []
                    route_state.failure_chain = [
                        dict(item) for item in saved_failures if isinstance(item, dict)
                    ][-_MAX_AI_FAILURE_HISTORY:]
                    route_state.attempt_count = max(
                        0, int(saved_route_state.get("attempt_count", 0) or 0)
                    )
                    route_state.success_count = max(
                        0, int(saved_route_state.get("success_count", 0) or 0)
                    )
                logger.info(
                    "Resuming translation from checkpoint (%d completed batches)",
                    len(completed_batches),
                )
            else:
                logger.info("Translation configuration changed; invalidating incompatible checkpoint")
        except Exception as cp_err:
            logger.warning("Could not read translation checkpoint: %s", cp_err)

    # A late dictionary entry applies to the entire job, including earlier
    # batches. Revalidate cached items together before deciding which IDs to
    # resume; dictionary order must not change their meaning.
    cached_items = []
    for start in range(0, len(out.segments), _TRANSLATION_BATCH_SIZE):
        key = str(start)
        cached = completed_batches.get(key)
        if not isinstance(cached, dict) or cached.get("failed"):
            completed_batches.pop(key, None)
            continue
        batch = out.segments[start:start + _TRANSLATION_BATCH_SIZE]
        items = cached.get("items")
        try:
            ids = {item["id"] for item in items}
            subset = [seg for seg in batch if seg.id in ids]
            _validate_translation_payload(json.dumps(items, ensure_ascii=False), subset)
            cached_items.extend(items)
        except (TypeError, KeyError, ValueError):
            completed_batches.pop(key, None)
    if cached_items:
        ids = {item["id"] for item in cached_items}
        cached_accepted, _, localized_terms = _translation_candidates(
            json.dumps(cached_items, ensure_ascii=False),
            [seg for seg in out.segments if seg.id in ids], {},
            profile, transcript.language, target_language, context_segments=out.segments)
        for cached in completed_batches.values():
            cached["items"] = [item for item in cached.get("items", [])
                               if item["id"] in cached_accepted]

    def save_checkpoint():
        if checkpoint_file is None:
            return
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_cp = checkpoint_file.with_suffix(".tmp")
        payload = {**checkpoint_meta, "batches": completed_batches,
            "route_state": {"sticky_index": route_state.sticky_index,
                "failure_chain": list(route_state.failure_chain),
                "attempt_count": route_state.attempt_count,
                "success_count": route_state.success_count}}
        if auto_policy:
            payload["auto_drafts"] = auto_drafts
        tmp_cp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp_cp.replace(checkpoint_file)

    for start in range(0, len(out.segments), _TRANSLATION_BATCH_SIZE):
        await _cancel_checkpoint(cancel_check, "translation")
        batch = out.segments[start:start + _TRANSLATION_BATCH_SIZE]
        batch_key = str(start)
        cached = completed_batches.get(batch_key)
        accepted_items = {}
        if isinstance(cached, dict) and not cached.get("failed"):
            try:
                items = cached.get("items")
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise ValueError("Invalid checkpoint items")
                ids = [item.get("id") for item in items]
                if any(not isinstance(seg_id, str) for seg_id in ids) or len(set(ids)) != len(ids):
                    raise ValueError("Invalid checkpoint IDs")
                subset = [segment for segment in batch if segment.id in ids]
                if len(subset) != len(ids):
                    raise ValueError("Unknown checkpoint IDs")
                if cached.get("version") != 2 and len(subset) != len(batch):
                    raise ValueError("Legacy checkpoint must be complete")
                accepted_items, _rejected, accepted_terms = _translation_candidates(
                    json.dumps(items, ensure_ascii=False), subset, localized_terms,
                    profile, transcript.language, target_language, context_segments=out.segments)
                localized_terms.update(accepted_terms)
            except (TypeError, KeyError, ValueError):
                completed_batches.pop(batch_key, None)
                logger.warning("Discarding invalid translation checkpoint batch %s", batch_key)

        async def keep_accepted(additions):
            accepted_items.update(additions)
            for segment in batch:
                if segment.id in accepted_items:
                    segment.translated_text = accepted_items[segment.id]["text"]
                    segment.translation_status = "success"
            # Store explicit IDs, never positional partial texts. Names are
            # saved only alongside accepted translations that actually use them.
            items = [{"id": segment.id, "text": accepted_items[segment.id]["text"],
                "localized_terms": {source: target for source, target in localized_terms.items()
                    if source in segment.source_text and target in accepted_items[segment.id]["text"]}}
                for segment in batch if segment.id in accepted_items]
            completed_batches[batch_key] = {"version": 2, "items": items, "failed": [],
                "pending_ids": [segment.id for segment in batch if segment.id not in accepted_items]}
            save_checkpoint()

        # Restore any audited draft from the checkpoint BEFORE deciding what to
        # re-translate: an already-audited draft is retained verbatim, never
        # promoted to success and never re-entering the job glossary.
        if auto_policy:
            for segment in batch:
                saved = saved_drafts.get(segment.id)
                if (segment.id not in accepted_items and segment.id not in auto_drafts
                        and isinstance(saved, dict)
                        and saved.get("source_text") == segment.source_text
                        and str(saved.get("target_text") or "").strip()
                        and saved.get("issues")):
                    auto_drafts[segment.id] = saved
        pending = [segment for segment in batch
                   if segment.id not in accepted_items and segment.id not in auto_drafts]
        if pending:
            total_batches = max(
                1,
                (len(out.segments) + _TRANSLATION_BATCH_SIZE - 1) // _TRANSLATION_BATCH_SIZE,
            )
            batch_number = (start // _TRANSLATION_BATCH_SIZE) + 1
            await _emit_ai_progress(progress_cb, {
                "event": "batch",
                "stage": "translation",
                "batch": batch_number,
                "total_batches": total_batches,
            })
            try:
                await _translate_batch_resilient(
                    ai, pending, route_state=route_state,
                    target_language=target_language,
                    context=context,
                    source_language=transcript.language,
                    context_segments=out.segments,
                    profile=profile,
                    for_dub=for_dub,
                    fallback_ai=fallback_ai,
                    progress_cb=progress_cb,
                    localized_terms=localized_terms,
                    accepted_cb=keep_accepted,
                    drafts=auto_drafts if auto_policy else None,
                    cancel_check=cancel_check,
                )
            finally:
                # Also persist pending IDs and route audit on transport failure
                # or cancellation, after the last durable accepted response.
                await keep_accepted({})
        # A resumed audited draft is restored verbatim; it is NEVER promoted to
        # success and never re-enters the job glossary. Only a fresh rejection
        # overwrites it with the current attempt's audit.
        if auto_policy:
            for segment in batch:
                saved = saved_drafts.get(segment.id)
                if (segment.id not in accepted_items and segment.id not in auto_drafts
                        and isinstance(saved, dict)
                        and saved.get("source_text") == segment.source_text
                        and str(saved.get("target_text") or "").strip()
                        and saved.get("issues")):
                    auto_drafts[segment.id] = saved

        texts = [accepted_items[segment.id]["text"] if segment.id in accepted_items
                 else segment.source_text for segment in batch]
        failed = {index for index, segment in enumerate(batch) if segment.id not in accepted_items}

        failed_total += len(failed)
        for idx, (seg, text) in enumerate(zip(batch, texts)):
            seg.translated_text = text
            seg.translation_status = "untranslated" if idx in failed else "success"
            if auto_policy and seg.id in auto_drafts:
                # Structurally valid but semantically rejected text is retained
                # ONLY as a warning-marked draft; it never becomes success.
                seg.translated_text = auto_drafts[seg.id]["target_text"]
                seg.translation_status = "draft"
                seg.qc_status = "review"
            if not for_dub:
                seg.target_start_ms = seg.source_start_ms
                seg.target_end_ms = seg.source_end_ms
                seg.timing_status = "fit"
                seg.qc_status = ("review" if idx in failed or seg.translation_status == "draft"
                                 else "pass")

    # Terms introduced later cannot leave an earlier, inconsistent spelling
    # marked successful. Keep only genuinely accepted IDs in the checkpoint;
    # a resume repairs these IDs using the final dictionary.
    final_checks = _translation_guard_checks(out.segments,
        [s.translated_text if s.translation_status == 'success' else '' for s in out.segments],
        profile, transcript.language, target_language, localized_terms, out.segments)
    guard_reviews = {}
    for index, seg in enumerate(out.segments):
        if seg.translation_status != "success":
            continue
        violations = [issue for issue in final_checks[seg.id] if issue.get('severity') != 'uncertain']
        advisories = [issue for issue in final_checks[seg.id] if issue.get('severity') == 'uncertain']
        if advisories and not violations:
            guard_reviews[seg.id] = advisories
            seg.qc_status = "review"
        if violations:
            if auto_policy and str(seg.translated_text or "").strip():
                auto_drafts[seg.id] = {
                    "segment_id": seg.id,
                    "source_text": seg.source_text, "target_text": seg.translated_text,
                    "issues": violations, "reasons": [issue.get("kind") for issue in violations],
                    "attempts": 1, "semantic_attempts": 1, "repair_attempted": False,
                    "origin": "final_global_guard",
                    "source_start_ms": seg.source_start_ms, "source_end_ms": seg.source_end_ms,
                    "speaker_id": seg.speaker_id,
                }
                seg.translation_status = "draft"
            else:
                seg.translated_text = seg.source_text
                seg.translation_status = "untranslated"
            seg.qc_status = "review"
            failed_total += 1
            cached = completed_batches[str(index // _TRANSLATION_BATCH_SIZE * _TRANSLATION_BATCH_SIZE)]
            cached["items"] = [item for item in cached["items"] if item["id"] != seg.id]
            if seg.id not in cached["pending_ids"]:
                cached["pending_ids"].append(seg.id)
    draft_ids = {seg.id for seg in out.segments if seg.translation_status == "draft"}
    if auto_policy and draft_ids and source_media is not None:
        await _recheck_pending_sources(
            out, sorted(draft_ids), auto_drafts,
            source_media=Path(source_media), profile=profile)
    # Global guard may demote a formerly accepted item. Learn terms only from
    # the final successful set, never from a draft kept solely for continuity.
    accepted_ids = {seg.id for seg in out.segments if seg.translation_status == "success"}
    localized_terms.clear()
    for cached in completed_batches.values():
        for item in cached.get("items", []):
            if item["id"] in accepted_ids:
                localized_terms.update(item.get("localized_terms") or {})
    if auto_policy:
        # Drafts are a documented, warning-marked continuation state, not a
        # missing translation. They never count as a hard failure and never
        # satisfy the strict-QC abort; a reviewer still sees the exact audit.
        failed_total = max(0, failed_total - len(draft_ids))
        if draft_ids:
            out.metadata["translation_auto_drafts"] = auto_drafts
        # Only an actual rejected candidate after bounded repair may be omitted.
        # Provider/empty/malformed failures have no such receipt and stay failed.
        from backend.services.dubbing_rewrite_guard import untranslated_han
        from backend.services.localization_omissions import has_cue_local_repair_evidence
        omissions = {}
        for seg in out.segments:
            record = auto_drafts.get(seg.id, {})
            if (seg.translation_status == "draft"
                    and has_cue_local_repair_evidence(seg, record)
                    and untranslated_han(seg.translated_text or "", target_language,
                        allowed=[*profile.protected_terms, *profile.glossary.values()])):
                omissions[seg.id] = {**record,
                    "rejected_target_text": seg.translated_text,
                    "reason": "unreadable_after_bounded_repair"}
                seg.translated_text = ""
                seg.translation_status = "omitted"
                seg.qc_status = "review"
        if omissions:
            out.metadata["translation_omissions"] = omissions
            from backend.services.localization_omissions import validate_translation_omissions
            validate_translation_omissions(out, profile)
    save_checkpoint()

    if failed_total == len(out.segments):
        exc = RuntimeError("Translation failed for every segment.")
        raise _attach_ai_failure_context(exc, "translation", route_state)
    if failed_total and profile.qc_profile == "strict":
        exc = RuntimeError(
            f"Translation left {failed_total} segment(s) untranslated under strict QC."
        )
        raise _attach_ai_failure_context(exc, "translation", route_state)
    out.metadata["translation_ai"] = {
        "selected_connection_id": _selected_ai_connection_id(route_state),
        "route_chain": route_fingerprint,
        "failure_chain": list(route_state.failure_chain),
        "sticky_route_index": route_state.sticky_index,
        "attempted": route_state.attempt_count > 0,
        "attempt_count": route_state.attempt_count,
        "success_count": route_state.success_count,
    }
    out.metadata["translation_guard_review"] = guard_reviews
    out.metadata["localized_terms"] = localized_terms
    out.metadata["localization_policy_version"] = 3
    out.metadata["target_language"] = target_language
    return out


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_call_ai_chat_resilient` is owned here but patched as an attribute of
# localization_service by tests and donor integrations. Its body lives in
# `_call_ai_chat_resilient_impl`; this public name is the seam the internal
# call site resolves through the facade, so a patch on the facade still takes
# effect. Before the split the definition and every call site shared one
# module namespace, so a patch was visible without any indirection.
async def _call_ai_chat_resilient(*args, **kwargs):
    # Straight to the implementation. The dispatch seam lives at the CALL
    # SITE (`_translate_batch_resilient` resolves `_facade._call_ai_chat_resilient`),
    # which is the only place a test double has to intercept. Routing this
    # wrapper back through the facade instead would recurse: the facade
    # attribute IS this wrapper.
    return await _call_ai_chat_resilient_impl(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_profile_ai_route_clients_compat` is patched as an attribute of localization_service by tests and
# donor integrations. A direct import would bind the original object here and
# the patch would silently stop reaching the call site, so this proxy looks
# the attribute up on the facade at call time instead.
def _profile_ai_route_clients_compat(*args, **kwargs):
    return getattr(_facade, "_profile_ai_route_clients_compat")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_profile_ai_route_specs` is patched as an attribute of localization_service by tests and
# donor integrations. A direct import would bind the original object here and
# the patch would silently stop reaching the call site, so this proxy looks
# the attribute up on the facade at call time instead.
def _profile_ai_route_specs(*args, **kwargs):
    return getattr(_facade, "_profile_ai_route_specs")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_translation_guard_checks` is patched as an attribute of localization_service by tests and
# donor integrations. A direct import would bind the original object here and
# the patch would silently stop reaching the call site, so this proxy looks
# the attribute up on the facade at call time instead.
def _translation_guard_checks(*args, **kwargs):
    return getattr(_facade, "_translation_guard_checks")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_AIStageRouteState` is observed or patched as an attribute of localization_service, so a
# direct import here would bind the original object and the patch would stop
# reaching this call site. Resolve it through the facade at call time.
def _AIStageRouteState(*args, **kwargs):
    return getattr(_facade, "_AIStageRouteState")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_append_ai_failure` is observed or patched as an attribute of localization_service, so a
# direct import here would bind the original object and the patch would stop
# reaching this call site. Resolve it through the facade at call time.
def _append_ai_failure(*args, **kwargs):
    return getattr(_facade, "_append_ai_failure")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_translation_candidates` is observed or patched as an attribute of localization_service, so a
# direct import here would bind the original object and the patch would stop
# reaching this call site. Resolve it through the facade at call time.
def _translation_candidates(*args, **kwargs):
    return getattr(_facade, "_translation_candidates")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_translation_prompt` is observed or patched as an attribute of localization_service, so a
# direct import here would bind the original object and the patch would stop
# reaching this call site. Resolve it through the facade at call time.
def _translation_prompt(*args, **kwargs):
    return getattr(_facade, "_translation_prompt")(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_extract_reference_clip_sync` is observed or patched as an attribute of localization_service, so a
# direct import here would bind the original object and the patch would stop
# reaching this call site. Resolve it through the facade at call time.
def _extract_reference_clip_sync(*args, **kwargs):
    return getattr(_facade, "_extract_reference_clip_sync")(*args, **kwargs)
