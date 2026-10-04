"""Shared concurrency primitives for in-process task limiting.

Lives in its own module (not in task_runner.py) so services that must not
import task_runner — notably backend/services/clip_extractor.py — can share a
process-wide semaphore without creating an import cycle.
"""
import asyncio
import random
import weakref


# Process-level cap on parallel ffmpeg subprocesses kicked off from a
# single long-running job (e.g. the per-clip parallel pipeline in
# clip_extractor that would otherwise fire N=max_clips ffmpegs at once).
# Each ffmpeg uses multiple threads internally; without a cap, an 18-clip
# extract spawns 18 parallel ffmpegs ≈ 100+ threads competing for cores
# and pegs the CPU hard enough that even a /api/jobs DB read gets pushed
# off the scheduler — UI feels frozen during heavy jobs.
#
# Cap=4 leaves plenty of headroom on a typical 8-core laptop while still
# parallelising the per-clip phases. Wall-clock time on big jobs goes up
# (4-batch sweep instead of one-shot) but UI stays responsive. Tune up if
# you have a big machine and don't care about UI latency during processing.
_ffmpeg_semaphore = asyncio.Semaphore(4)


class AIProviderCooldownError(RuntimeError):
    """Admission cannot fit the caller's deadline; no HTTP request was sent."""


class AIRequestBudgetError(RuntimeError):
    """A caller-owned dispatch budget has been exhausted."""


class _AIGate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.last_finished = float('-inf')
        self.cooldown_until = 0.0
        self.rate_failures = 0
        self.min_gap_s = 0.0
        self.cooldown_initial_s = 0.0
        self.cooldown_max_s = 0.0

    def configure(self, policy):
        # An alias with weaker settings cannot shorten a shared resource's wait.
        for field in ('min_gap_s', 'cooldown_initial_s', 'cooldown_max_s'):
            setattr(self, field, max(getattr(self, field), policy[field]))

    def rate_failed(self, now, retry_after):
        self.rate_failures += 1
        delay = min(self.cooldown_max_s,
                    self.cooldown_initial_s * (2 ** min(self.rate_failures - 1, 20)))
        delay = min(self.cooldown_max_s, delay + random.uniform(0, min(1.0, delay * 0.05)))
        # A server hint is never shortened by the local cap or policy aliases.
        delay = max(delay, retry_after or 0.0)
        self.cooldown_until = max(self.cooldown_until, now + delay)


# Loop-local state avoids cross-loop asyncio.Lock reuse. One backend process,
# not a durable or cross-worker scheduler. Endpoint aliases and explicit groups
# both resolve to the SAME state; a new label cannot bypass an existing gate.
_ai_gates = weakref.WeakKeyDictionary()


def get_ai_gate(endpoint: str, group: str | None, policy: dict):
    loop = asyncio.get_running_loop()
    endpoints, groups = _ai_gates.setdefault(loop, ({}, {}))
    by_endpoint = endpoints.get(endpoint)
    by_group = groups.get(group) if group else None
    if by_endpoint is not None and by_group is not None and by_endpoint is not by_group:
        raise ValueError('AI resource group conflicts with an already active endpoint gate')
    gate = by_endpoint or by_group or _AIGate()
    endpoints[endpoint] = gate
    if group:
        groups[group] = gate
    gate.configure(policy)
    return gate
