# SPDX-License-Identifier: AGPL-3.0-only
"""Real HTTP / canonical SDK integration; controlled upstream, not LIVE DeepSeek."""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from aiohttp import web

from backend.core import ai_provider as ap


def policy(**values):
    return dict(enabled=True, min_gap_s=0.06, cooldown_initial_s=0.08,
                cooldown_max_s=0.25, **values)


def completion():
    return {"id": "qa", "object": "chat.completion", "created": 1, "model": "qa",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Xin chào."},
                         "finish_reason": "stop"}]}


@asynccontextmanager
async def upstream(handler=None):
    rows = []
    async def serve(request):
        payload = await request.json()
        row = {"start": time.monotonic(), "payload": payload}
        rows.append(row)
        try:
            if handler:
                return await handler(request, payload, len(rows))
            await asyncio.sleep(0.025)
            return web.json_response(completion())
        finally:
            row["end"] = time.monotonic()
    app = web.Application()
    app.router.add_post('/v1/chat/completions', serve)
    runner = web.AppRunner(app, shutdown_timeout=0.2)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1'
    try:
        yield base, rows
    finally:
        await runner.cleanup()


def client(base, p=None, model='qa'):
    return ap.make_ai_client('compatible', model=model, base_url=base,
                             traffic_policy=p if p is not None else policy())


async def say(ai, context=None):
    kwargs = dict(messages=[{"role": "user", "content": "Translate Hello to Vietnamese."}], max_tokens=80)
    if context is not None:
        kwargs['request_context'] = context
    return await ai.chat(**kwargs)


@pytest.mark.asyncio
async def test_independent_clients_share_gap_after_previous_response():
    async with upstream() as (base, rows):
        one, two = client(base), client(base + '/', model='other-model')
        results = await asyncio.gather(say(one), say(two), say(one))
        assert results == ['Xin chào.'] * 3
        assert len(rows) == 3
        assert all(b['start'] - a['end'] >= 0.05 for a, b in zip(rows, rows[1:]))


@pytest.mark.asyncio
async def test_rate_502_publishes_cooldown_and_only_one_queued_recovery():
    async def handler(request, payload, n):
        if n == 1:
            return web.json_response({'error': {'message': 'Bạn gửi tin quá thường xuyên'}}, status=502,
                                     headers={'Retry-After': '0.16'})
        await asyncio.sleep(0.025)
        return web.json_response(completion())
    async with upstream(handler) as (base, rows):
        one, two = client(base), client(base)
        results = await asyncio.gather(say(one), say(two), say(one), return_exceptions=True)
        assert isinstance(results[0], Exception)
        assert results[1:] == ['Xin chào.'] * 2
        assert len(rows) == 3, 'SDK must not retry the initial 502 internally'
        assert rows[1]['start'] - rows[0]['end'] >= 0.15
        assert rows[2]['start'] - rows[1]['end'] >= 0.05


@pytest.mark.asyncio
async def test_cooldown_deadline_and_cancel_waiters_dispatch_nothing_extra():
    async def handler(request, payload, n):
        return web.json_response({'error': {'message': 'Too many requests'}}, status=429,
                                 headers={'Retry-After': '1'})
    async with upstream(handler) as (base, rows):
        ai = client(base)
        with pytest.raises(Exception):
            await say(ai)
        ctx = ap.AIRequestContext(deadline_at=asyncio.get_running_loop().time() + 0.1)
        with pytest.raises(ap.AIProviderCooldownError):
            await say(ai, ctx)
        task = asyncio.create_task(say(ai))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_admission_wait_does_not_consume_wire_timeout_and_budget_counts_dispatch():
    class Budget:
        count = 0
        def consume(self):
            if self.count >= 1:
                raise ap.AIRequestBudgetError('one dispatch only')
            self.count += 1
    async with upstream() as (base, rows):
        ai = client(base)
        await say(ai)
        events, budget = [], Budget()
        ctx = ap.AIRequestContext(deadline_at=asyncio.get_running_loop().time() + 2,
                                  request_timeout_s=0.05, progress_cb=events.append, dispatch_budget=budget)
        assert await say(ai, ctx) == 'Xin chào.'
        assert budget.count == 1
        with pytest.raises(ap.AIRequestBudgetError):
            await say(ai, ctx)
        assert len(rows) == 2
        assert any(e['event'] == 'provider_wait' for e in events)
        assert sum(e['event'] == 'http_dispatch' for e in events) == 1


@pytest.mark.asyncio
async def test_wire_timeout_releases_slot_other_endpoint_remains_independent():
    async def slow(request, payload, n):
        await asyncio.sleep(0.18)
        return web.json_response(completion())
    async with upstream(slow) as (base, rows), upstream() as (other, elsewhere):
        task = asyncio.create_task(say(client(base), ap.AIRequestContext(request_timeout_s=0.05)))
        assert await say(client(other)) == 'Xin chào.'
        with pytest.raises(TimeoutError):
            await task
        assert len(rows) == len(elsewhere) == 1


@pytest.mark.asyncio
async def test_stream_holds_admission_until_consumed_and_close_releases():
    released = asyncio.Event()
    async def handler(request, payload, n):
        if not payload.get('stream'):
            return web.json_response(completion())
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        part = dict(id='qa', object='chat.completion.chunk', created=1, model='qa',
                    choices=[dict(index=0, delta={'content': 'Xin'}, finish_reason=None)])
        await response.write(('data: ' + json.dumps(part) + '\n\n').encode())
        await released.wait()
        try:
            await response.write(b'data: [DONE]\n\n')
            await response.write_eof()
        except ConnectionResetError:
            pass
        return response
    async with upstream(handler) as (base, rows):
        ai = client(base)
        stream = ai.chat_stream(messages=[{'role': 'user', 'content': 'Hello'}])
        assert await anext(stream) == 'Xin'
        queued = asyncio.create_task(say(client(base)))
        try:
            await asyncio.sleep(0.09)
            assert len(rows) == 1
            await stream.aclose()
        except BaseException:
            queued.cancel()
            raise
        finally:
            released.set()
            await stream.aclose()
        assert await queued == 'Xin chào.'
        assert len(rows) == 2


@pytest.mark.parametrize('status,body,expected', [
    (429, 'quota', True), (502, 'Bạn gửi tin quá thường xuyên', True),
    (502, 'Too many messages', True), (502, 'ordinary bad gateway', False),
    (502, 'no data.biz_data.challenge', False), (401, 'too many requests', False),
])
def test_rate_classification_is_body_specific(status, body, expected):
    exc = type('UpstreamError', (RuntimeError,), {'status_code': status, 'body': {'error': {'message': body}}})(body)
    assert ap.is_ai_rate_limit_error(exc) is expected


def test_rate_classification_reads_code_when_status_code_is_absent():
    # Non-OpenAI wrappers/doubles expose HTTP state via .code only. If the
    # rate classifier ignores it, a real 429 is treated as a plain transient
    # error: retried up to the transport cap with NO shared cooldown published,
    # which is exactly the amplification this gate exists to prevent.
    for attribute in ('code', 'status_code'):
        exc = type('UpstreamError', (RuntimeError,), {attribute: 429})()
        assert ap.is_ai_rate_limit_error(exc) is True
        assert ap.is_transient_ai_error(exc) is True
    # A non-rate code must still not publish a cooldown.
    exc = type('UpstreamError', (RuntimeError,), {'code': 502})()
    assert ap.is_ai_rate_limit_error(exc) is False


def test_retry_after_seconds_date_and_invalid():
    assert ap.parse_ai_retry_after('600') == 600
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=3), usegmt=True)
    assert 1.5 <= ap.parse_ai_retry_after(future) <= 3
    for bad in ('-1', 'nan', 'inf', 'garbage', ''):
        assert ap.parse_ai_retry_after(bad) is None


@pytest.mark.asyncio
async def test_explicit_group_aliases_share_gate_and_same_endpoint_cannot_change_group_to_bypass():
    p = policy(resource_group='qa-shared')
    async with upstream() as (base, rows), upstream() as (other, others):
        await say(client(base, p))
        await say(client(other, p))
        assert others[0]['start'] - rows[0]['end'] >= 0.05
        await say(client(base, policy(resource_group='second-label')))
        assert rows[-1]['start'] - others[0]['end'] >= 0.05


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,streaming', [('anthropic', False), ('anthropic', True), ('openrouter', True)])
async def test_all_paced_transports_disable_sdk_retries(monkeypatch, provider, streaming):
    import anthropic
    import openai
    seen = []
    def stop(**kwargs):
        seen.append(kwargs)
        raise RuntimeError('constructor sentinel; no network')
    monkeypatch.setattr(anthropic, 'AsyncAnthropic', stop)
    monkeypatch.setattr(openai, 'AsyncOpenAI', stop)
    ai = ap.AIClient(ap.AIProvider(provider), 'fake-key', traffic_policy=policy())
    with pytest.raises(RuntimeError, match='sentinel'):
        if streaming:
            await anext(ai.chat_stream(messages=[]))
        else:
            await say(ai)
    assert seen[0].get('max_retries') == 0


def test_invalid_saved_policy_fails_closed_instead_of_disabling_pacing():
    from types import SimpleNamespace
    settings = SimpleNamespace(ai_connection_id='paced', ai_connections_json=json.dumps({
        'paced': {'traffic_policy': {'enabled': True, 'min_gap_s': -1}},
    }))
    with pytest.raises(ValueError):
        ap._resolve_traffic_policy(settings)


def test_exponential_cooldown_cap_and_long_server_hint(monkeypatch):
    from backend.core.concurrency import _AIGate
    monkeypatch.setattr('backend.core.concurrency.random.uniform', lambda *_: 1.0)
    gate = _AIGate()
    gate.configure(ap.normalize_ai_traffic_policy({'enabled': True}))
    delays = []
    for _ in range(5):
        gate.rate_failed(0, None)
        delays.append(gate.cooldown_until)
    assert delays == [31, 61, 121, 241, 300]
    gate.rate_failed(0, 600)
    assert gate.cooldown_until == 600


@pytest.mark.asyncio
async def test_explicit_group_shares_cooldown_across_distinct_endpoints():
    async def rate(request, payload, n):
        return web.json_response({'error': {'message': 'Too many requests'}}, status=429)
    async with upstream(rate) as (base_a, rows_a), upstream() as (base_b, rows_b):
        grouped = {**policy(), 'min_gap_s': 0, 'resource_group': 'one-account'}
        a, b = client(base_a, grouped), client(base_b, grouped)
        with pytest.raises(Exception) as error:
            await say(a)
        assert getattr(error.value, 'status_code', None) == 429
        await say(b)
        assert rows_b[0]['start'] - rows_a[0]['end'] >= 0.075


@pytest.mark.asyncio
async def test_conflicting_existing_group_bridge_fails_closed_before_dispatch():
    async with upstream() as (base_a, rows_a), upstream() as (base_b, rows_b):
        a = client(base_a, {**policy(), 'resource_group': 'account-a'})
        b = client(base_b, {**policy(), 'resource_group': 'account-b'})
        await asyncio.gather(say(a), say(b))
        bridge = client(base_a, {**policy(), 'resource_group': 'account-b'})
        with pytest.raises(ValueError, match='conflict'):
            await say(bridge)
        assert len(rows_a) == len(rows_b) == 1


@pytest.mark.asyncio
async def test_slow_dispatch_observer_cannot_spend_budget_without_http_send():
    callback_done = asyncio.Event()
    class Budget:
        count = 0
        def consume(self):
            self.count += 1
    async def callback(event):
        if event['event'] == 'http_dispatch':
            await asyncio.sleep(0.2)
            callback_done.set()
    budget = Budget()
    async with upstream() as (base, rows):
        context = ap.AIRequestContext(deadline_at=asyncio.get_running_loop().time() + 0.1,
                                      dispatch_budget=budget, progress_cb=callback)
        assert await say(client(base), context) == 'Xin chào.'
        assert budget.count == len(rows) == 1
        await asyncio.wait_for(callback_done.wait(), 0.5)
