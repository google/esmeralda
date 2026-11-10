# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A2A client timeout and the retry decision table (esmeralda.a2a_retry)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from esmeralda import a2a, a2a_retry

URL = "https://specialist.esmeralda.internal/v1/message:send"


# --- timeout ------------------------------------------------------------------------------------


def test_timeout_defaults_to_300(monkeypatch):
    monkeypatch.delenv("A2A_TIMEOUT_SECONDS", raising=False)
    assert a2a_retry.client_timeout() == 300.0


def test_timeout_from_env(monkeypatch):
    monkeypatch.setenv("A2A_TIMEOUT_SECONDS", "120")
    assert a2a_retry.client_timeout() == 120.0


def test_explicit_timeout_wins_over_env(monkeypatch):
    monkeypatch.setenv("A2A_TIMEOUT_SECONDS", "120")
    assert a2a_retry.client_timeout(45) == 45.0


@pytest.mark.parametrize("raw", ["0", "-5", "soon"])
def test_invalid_timeout_env_fails_fast(monkeypatch, raw):
    monkeypatch.setenv("A2A_TIMEOUT_SECONDS", raw)
    with pytest.raises(ValueError):
        a2a_retry.client_timeout()


def test_remote_agent_uses_env_timeout_and_retry_transport(monkeypatch):
    monkeypatch.setenv("A2A_TIMEOUT_SECONDS", "250")
    agent = a2a.remote_agent("specialist", url="https://specialist.esmeralda.internal")
    client = agent._httpx_client
    assert client.timeout.read == 250.0
    assert client.timeout.connect == 250.0
    assert isinstance(client._transport, a2a_retry.RetryTransport)


# --- decision table -----------------------------------------------------------------------------


def _request_error(cls: type[httpx.RequestError]) -> httpx.RequestError:
    return cls("boom", request=httpx.Request("POST", URL))


@pytest.mark.parametrize(
    ("failure", "retry"),
    [
        ({"error": _request_error(httpx.ConnectError)}, True),  # refused / DNS / TLS handshake
        ({"error": _request_error(httpx.ConnectTimeout)}, True),
        ({"error": _request_error(httpx.PoolTimeout)}, True),
        ({"status": 429}, True),
        ({"status": 502}, True),
        ({"status": 503}, True),
        ({"status": 502, "task_created": True}, False),
        ({"status": 503, "task_created": True}, False),
        ({"status": 504}, False),  # the callee keeps running: never re-send
        ({"error": _request_error(httpx.ReadTimeout)}, False),  # same
        ({"error": _request_error(httpx.WriteTimeout)}, False),
        ({"error": _request_error(httpx.RemoteProtocolError)}, False),
        ({"error": _request_error(httpx.ReadError)}, False),
        ({"status": 500}, False),
        ({"status": 400}, False),
        ({"status": 401}, False),
        ({"status": 403}, False),
        ({"status": 404}, False),
        ({"error": RuntimeError("unexpected")}, False),
    ],
)
def test_decision_table(failure, retry):
    assert a2a_retry.should_retry(1, **failure) is retry


def test_at_most_three_attempts():
    assert a2a_retry.should_retry(2, status=503) is True
    assert a2a_retry.should_retry(3, status=503) is False
    assert a2a_retry.should_retry(3, error=_request_error(httpx.ConnectError)) is False


@pytest.mark.parametrize("attempt", [1, 2])
def test_backoff_is_jittered_exponential(attempt):
    delay = a2a_retry.BASE_DELAY_SECONDS * 2 ** (attempt - 1)
    samples = {a2a_retry.backoff_seconds(attempt) for _ in range(50)}
    assert all(delay / 2 <= s <= delay for s in samples)
    assert len(samples) > 1


def test_backoff_honors_short_retry_after_only():
    assert a2a_retry.backoff_seconds(1, retry_after=3) == 3
    assert a2a_retry.backoff_seconds(1, retry_after=600) == a2a_retry.MAX_RETRY_AFTER_SECONDS


@pytest.mark.parametrize(
    ("body", "created"),
    [
        (b"<html>no healthy upstream</html>", False),
        (b"", False),
        (json.dumps({"error": {"code": 503, "message": "unavailable"}}).encode(), False),
        (json.dumps({"id": "t-1", "status": {"state": "working"}}).encode(), True),
        (json.dumps({"error": {"data": {"taskId": "t-1"}}}).encode(), True),
    ],
)
def test_task_created_detection(body, created):
    assert a2a_retry.task_created(httpx.Response(503, content=body)) is created


# --- transport ----------------------------------------------------------------------------------


class _Scripted(httpx.AsyncBaseTransport):
    """Returns (or raises) the scripted outcomes in order and records each request body."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.bodies: list[bytes] = []

    async def handle_async_request(self, request):
        self.bodies.append(await request.aread())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _send(outcomes):
    inner = _Scripted(outcomes)
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async def run():
        async with httpx.AsyncClient(transport=a2a_retry.RetryTransport(inner, sleep=sleep)) as client:
            return await client.post(URL, json={"message": "hi"})

    try:
        response = asyncio.run(run())
    except Exception as exc:  # surfaced to the caller
        response = exc
    return response, inner, sleeps


def test_transport_retries_connect_error_then_succeeds():
    response, inner, sleeps = _send([_request_error(httpx.ConnectError), httpx.Response(200, json={"ok": 1})])
    assert response.status_code == 200
    assert len(inner.bodies) == 2
    assert inner.bodies[0] == inner.bodies[1] == b'{"message":"hi"}'  # the body is re-sent intact
    assert len(sleeps) == 1


def test_transport_retries_503_then_429_then_succeeds():
    response, inner, sleeps = _send(
        [httpx.Response(503, text="no healthy upstream"), httpx.Response(429), httpx.Response(200)]
    )
    assert response.status_code == 200
    assert len(inner.bodies) == 3
    assert len(sleeps) == 2


def test_transport_gives_up_after_three_attempts():
    response, inner, _ = _send([httpx.Response(503)] * 3)
    assert response.status_code == 503
    assert len(inner.bodies) == 3


def test_transport_never_resends_on_504():
    response, inner, sleeps = _send([httpx.Response(504), httpx.Response(200)])
    assert response.status_code == 504
    assert len(inner.bodies) == 1
    assert sleeps == []


def test_transport_never_resends_on_read_timeout():
    response, inner, sleeps = _send([_request_error(httpx.ReadTimeout), httpx.Response(200)])
    assert isinstance(response, httpx.ReadTimeout)
    assert len(inner.bodies) == 1
    assert sleeps == []


def test_transport_does_not_resend_when_a_task_exists():
    body = {"id": "t-1", "status": {"state": "working"}}
    response, inner, _ = _send([httpx.Response(503, json=body), httpx.Response(200)])
    assert response.status_code == 503
    assert response.json() == body  # the body is still readable by the caller
    assert len(inner.bodies) == 1
