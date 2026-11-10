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

"""tests/partner/probe.py against an in-process fake front door (loopback HTTP, no network)."""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from partner_testlib import PARTNER_DIR, load

sc = load("scenarios", PARTNER_DIR / "scenarios.py")
probe = load("partner_probe", PARTNER_DIR / "probe.py")
KEY = "test-partner-key"
SEEN: list[dict] = []


class FakeFrontDoor(BaseHTTPRequestHandler):
    """Answers like the partner proxies: 401 without the key, 403 for blocked targets, SSE for the orchestrator."""

    protocol_version = "HTTP/1.1"
    quota_left = 2

    def log_message(self, *_args) -> None:
        pass

    def _reply(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null")
        SEEN.append({"path": self.path, "host": self.headers.get("Host"), "key": self.headers.get("x-api-key")})
        if self.headers.get("x-api-key") != KEY:
            return self._reply(401, b'{"fault":"InvalidApiKey"}')
        if "ai-coe-mortgage-specialist" in self.path:
            return self._reply(403, b'{"error":"agent_not_allowed"}')
        if self.path.endswith("/mcp"):
            if body["method"] == "tools/call":
                return self._reply(403, b'{"error":"tool_forbidden"}')
            return self._reply(200, b'{"result":{"tools":[{"name":"verify_applicant"}]}}')
        if self.path.endswith(":streamQuery?alt=sse"):
            if self.headers.get("Accept") != "text/event-stream":  # the quota probe (JSON route)
                FakeFrontDoor.quota_left -= 1
                if FakeFrontDoor.quota_left < 0:
                    return self._reply(429, b'{"fault":"TokenQuotaViolation"}')
                return self._reply(200, b"{}")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for event in (b'data: {"content":"thinking"}\n\n', b'data: {"content":"Julian Sterling verified"}\n\n'):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(event), event))
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n")
            return None
        return self._reply(404, b'{"error":"not_found"}')

    do_GET = _handle
    do_POST = _handle


@pytest.fixture()
def server():
    SEEN.clear()
    FakeFrontDoor.quota_left = 2
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeFrontDoor)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def _factory(httpd):
    return lambda timeout: http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=timeout)


def test_every_probe_passes_against_a_conformant_front_door(server, capsys) -> None:
    results = [probe.run_probe(s, _factory(server), "partner.example.internal", KEY, quota=True) for s in sc.probes()]
    out = capsys.readouterr().out
    assert all(results), out
    assert "✅ Probe 4: Orchestrator SSE stream answered (TTFB=" in out
    assert "✅ Probe 5: Quota exhaustion rejected (HTTP 429)" in out
    assert {seen["host"] for seen in SEEN} == {"partner.example.internal"}
    assert KEY not in out


def test_output_parses_with_esmeralda_verify(server, capsys) -> None:
    runtime = pytest.importorskip("esmeralda.ops.runtime")
    for s in sc.probes():
        probe.run_probe(s, _factory(server), "partner.example.internal", KEY, quota=False)
    parsed = runtime.parse_partner_output(capsys.readouterr().out)
    assert parsed["mode"] == "live"
    assert [p["probe"] for p in parsed["probes"]] == [s.probe for s in sc.probes()]
    assert all(p["ok"] for p in parsed["probes"])


def test_destructive_probe_is_skipped_without_opt_in(server, capsys) -> None:
    quota = next(s for s in sc.probes() if s.destructive)
    assert probe.run_probe(quota, _factory(server), "h", KEY, quota=False)
    assert "not run" in capsys.readouterr().out
    assert not SEEN


def test_wrong_status_fails_with_reason(server, capsys) -> None:
    p4 = next(s for s in sc.probes() if s.stream)
    assert not probe.run_probe(p4, _factory(server), "h", "wrong-key", quota=False)
    out = capsys.readouterr().out
    assert "❌ Probe 4:" in out and "HTTP 401, expected 200" in out
    assert "wrong-key" not in out


def test_main_requires_configuration(capsys) -> None:
    assert probe.main({}) == 2
    assert "PARTNER_API_KEY" in capsys.readouterr().out
