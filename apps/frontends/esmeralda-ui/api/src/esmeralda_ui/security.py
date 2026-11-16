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

"""HTTP hardening, as plain ASGI middleware (it wraps every response, static files included).

* :class:`SecurityHeaders`: a strict Content Security Policy (``default-src 'self'``, no inline
  scripts or event handlers, no external fonts or CDNs, ``frame-ancestors 'none'``), plus
  ``X-Content-Type-Options``, ``Referrer-Policy``, ``X-Frame-Options`` and friends.
* :class:`RequestGuard`: the API is read-only, so only ``GET`` and ``HEAD`` are accepted and request
  bodies are refused (``Content-Length`` above :data:`MAX_BODY_BYTES`, or any chunked body).
* :class:`AuthGate`: every path except ``/healthz`` needs an identity (see :mod:`esmeralda_ui.auth`);
  failures are a generic 401 that says nothing about why.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from esmeralda_ui.auth import AuthError, Authenticator

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024
ALLOWED_METHODS = frozenset({"GET", "HEAD"})
PUBLIC_PATHS = frozenset({"/healthz"})

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}

Scope = dict[str, Any]


async def _send_json(send: Any, status: int, body: dict[str, Any], extra: list[tuple[bytes, bytes]] = ()) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()), *extra]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class SecurityHeaders:
    def __init__(self, app: Any, *, hsts: bool = False):
        self.app = app
        self.headers = [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
        if hsts:
            self.headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        self._names = {name for name, _ in self.headers} | {b"server"}

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in self._names]
                message = {**message, "headers": kept + self.headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestGuard:
    def __init__(self, app: Any, *, max_body_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] not in ALLOWED_METHODS:
            await _send_json(send, 405, {"error": "method not allowed"}, [(b"allow", b"GET, HEAD")])
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        if b"transfer-encoding" in headers:
            await _send_json(send, 413, {"error": "request body not accepted"})
            return
        length = headers.get(b"content-length")
        if length is not None:
            try:
                too_big = int(length) > self.max_body_bytes
            except ValueError:
                too_big = True
            if too_big:
                await _send_json(send, 413, {"error": "request body not accepted"})
                return
        await self.app(scope, receive, send)


class AuthGate:
    def __init__(self, app: Any, *, authenticator: Authenticator):
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            identity = self.authenticator.identify(headers)
        except AuthError as exc:
            log.info("unauthenticated request to %s: %s", scope.get("path"), exc)
            await _send_json(send, 401, {"error": "unauthenticated"})
            return
        except Exception:  # noqa: BLE001 - key fetch failures etc.: still a generic 401, details in the log
            log.exception("identity verification failed")
            await _send_json(send, 401, {"error": "unauthenticated"})
            return
        scope.setdefault("state", {})["identity"] = identity
        await self.app(scope, receive, send)
