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

"""Timeout and retry rules for A2A calls made by ``esmeralda.a2a.remote_agent``.

An A2A ``message:send`` is not idempotent: each attempt gets a new message ID, so the callee
can't dedupe, and a re-send runs the remote agent again. So only failures that happen **before**
the callee starts work are retried:

* connect errors (refused, DNS, TLS handshake) and pool timeouts: the request was never sent;
* ``502`` / ``503`` with no A2A task in the body: the load balancer or gateway had no backend;
* ``429``: rate limited before the call reached the agent.

Never retried: ``504`` and read timeouts (the callee keeps running and finishes, so a re-send
doubles the run), and every other status or error. Those are surfaced to the caller.

At most ``MAX_ATTEMPTS`` attempts, with jittered exponential backoff (about 0.5 s, 1 s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_ENV = "A2A_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 10.0

# httpx raises ConnectError for refused connections, DNS and TLS handshake failures.
_PRE_SEND_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_RETRY_STATUSES = frozenset({429, 502, 503})


def client_timeout(timeout: float | None = None) -> float:
    """The A2A client timeout: ``timeout`` if given, else ``$A2A_TIMEOUT_SECONDS``, else 300 s."""
    if timeout is not None:
        return float(timeout)
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{TIMEOUT_ENV} must be a positive number of seconds, got {raw!r}")
    return value


def should_retry(
    attempt: int, *, status: int | None = None, error: BaseException | None = None, task_created: bool = False
) -> bool:
    """Whether attempt number ``attempt`` (1-based), which failed this way, may be re-sent."""
    if attempt >= MAX_ATTEMPTS:
        return False
    if error is not None:
        return isinstance(error, _PRE_SEND_ERRORS)
    if status == 429:
        return True
    if status in (502, 503):
        return not task_created
    return False


def backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """Jittered exponential delay after attempt ``attempt``; honors a short ``Retry-After``."""
    delay = BASE_DELAY_SECONDS * 2 ** (attempt - 1)
    jittered = delay / 2 + random.uniform(0, delay / 2)
    if retry_after is not None:
        return max(jittered, min(retry_after, MAX_RETRY_AFTER_SECONDS))
    return jittered


def _has_task(body: Any, depth: int = 0) -> bool:
    if depth > 3 or not isinstance(body, dict):
        return False
    if {"task", "taskId", "task_id"} & body.keys():
        return True
    if "id" in body and "status" in body and isinstance(body["status"], dict):
        return True  # an A2A Task object
    return any(_has_task(value, depth + 1) for value in body.values())


def task_created(response: httpx.Response) -> bool:
    """Whether an error response says the callee already created an A2A task."""
    try:
        return _has_task(json.loads(response.content))
    except (ValueError, UnicodeDecodeError):
        return False


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None


class RetryTransport(httpx.AsyncBaseTransport):
    """httpx transport that re-sends a request only on the pre-execution failures above."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._sleep = sleep

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()  # buffer the body so it can be re-sent
        attempt = 1
        while True:
            retry_after = None
            try:
                response = await self._inner.handle_async_request(request)
            except Exception as exc:
                if not should_retry(attempt, error=exc):
                    raise
                reason = type(exc).__name__
            else:
                if response.status_code not in _RETRY_STATUSES:
                    return response
                await response.aread()
                if not should_retry(attempt, status=response.status_code, task_created=task_created(response)):
                    return response
                await response.aclose()
                reason, retry_after = str(response.status_code), _retry_after(response)
            delay = backoff_seconds(attempt, retry_after)
            logger.warning(
                "A2A %s %s failed before execution (%s); retry %d/%d in %.1fs",
                request.method, request.url, reason, attempt, MAX_ATTEMPTS - 1, delay,
            )
            await self._sleep(delay)
            attempt += 1

    async def aclose(self) -> None:
        await self._inner.aclose()
