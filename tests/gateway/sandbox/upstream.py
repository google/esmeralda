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

"""Upstream for the gateway sandbox (standard library only; runs on python:3.12-slim, no build).

Echoes each request as JSON. The Authorization header is reported as JWT claims, never as the raw
token. ``?delay=N`` waits N seconds (0–600) before answering; ``?alt=sse`` streams 3 SSE events 8 s apart.
"""

import base64
import binascii
import http.server
import json
import math
import os
import time
import urllib.parse

MAX_DELAY_S = 600


def bearer_claims(authorization: str) -> dict:
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or token.count(".") != 2:
        return {"kind": "opaque" if token else "none"}
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return {"kind": "malformed"}
    if not isinstance(claims, dict):
        return {"kind": "malformed"}
    return {"kind": "jwt", **{key: claims.get(key) for key in ("aud", "email", "iss", "exp")}}


def delay_seconds(query: dict[str, list[str]]) -> float | None:
    """The requested ``?delay=`` in seconds, or None when it isn't a number between 0 and MAX_DELAY_S."""
    try:
        delay = float(query.get("delay", ["0"])[0])
    except ValueError:
        return None
    return delay if math.isfinite(delay) and 0 <= delay <= MAX_DELAY_S else None


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle_any(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if query.get("alt") == ["sse"]:
            return self.stream_sse(events=3, gap_s=8.0)
        delay = delay_seconds(query)
        if delay is None:
            return self.reply(400, {"error": f"delay must be a number of seconds between 0 and {MAX_DELAY_S}"})
        time.sleep(delay)
        headers = {name.lower(): value for name, value in self.headers.items() if name.lower() != "authorization"}
        self.reply(
            200,
            {
                "method": self.command,
                "path": url.path,
                "query": query,
                "headers": headers,
                "authorization": bearer_claims(self.headers.get("Authorization", "")),
            },
        )

    def reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream_sse(self, events: int, gap_s: float) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index in range(events):
            if index:
                time.sleep(gap_s)
            chunk = f"data: {json.dumps({'event': index})}\n\n".encode()
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
