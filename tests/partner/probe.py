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

"""Live partner conformance probes, driven by ``scenarios.py`` (stdlib only).

``scripts/test_partner.sh <env>`` copies this file and ``scenarios.py`` to the test VM and runs
``python3 probe.py`` there. It sends every catalog scenario that has a ``probe`` number to
``https://$PARTNER_HOST`` (TCP to ``$PARTNER_ENTRY_IP``, TLS SNI and ``Host`` = the partner host,
like ``curl --resolve``), verified against ``$PARTNER_CA_FILE``, and prints one line per probe::

    ✅ Probe 1: Unauthenticated request rejected with HTTP 401
    ❌ Probe 4: Orchestrator SSE stream answered: HTTP 504, expected 200

``esmeralda verify --partner`` parses those lines (``esmeralda.ops.runtime.parse_partner_output``).
The API key is read from ``$PARTNER_API_KEY`` and is never printed. Destructive probes (quota
exhaustion) only run with ``ESMERALDA_TEST_QUOTA_EXHAUSTION=1``.
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scenarios import Scenario, probes  # noqa: E402

CONNECT_TIMEOUT = 5.0
EXCERPT = 300


class ResolvedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to ``host`` over a TCP connection to ``ip`` (the ``curl --resolve`` equivalent)."""

    def __init__(self, host: str, ip: str, context: ssl.SSLContext, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=context)
        self._ip = ip
        self._ssl_context = context

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), CONNECT_TIMEOUT)
        sock.settimeout(self.timeout)
        self.sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)


@dataclass
class Result:
    status: int
    text: str
    ttfb: Optional[float] = None  # seconds to the first response body byte
    total: float = 0.0


ConnFactory = Callable[[float], http.client.HTTPConnection]


def send(scenario: Scenario, conn_factory: ConnFactory, host: str, api_key: str, user_id: str) -> Result:
    """One request for ``scenario``; reads the whole body (streams included) within its timeout."""
    path, headers, body = scenario.render(user_id, uuid.uuid4().hex)
    headers = {"Host": host, **headers, **scenario.auth_headers(api_key)}
    started = time.monotonic()
    deadline = started + scenario.timeout
    conn = conn_factory(scenario.timeout)
    try:
        conn.request(scenario.method, path, body=body, headers=headers)
        resp = conn.getresponse()
        chunks: List[bytes] = []
        ttfb = None
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"response not complete after {scenario.timeout:.0f}s")
            chunk = resp.read1(65536)
            if not chunk:
                break
            if ttfb is None:
                ttfb = time.monotonic() - started
            chunks.append(chunk)
        total = time.monotonic() - started
        return Result(resp.status, b"".join(chunks).decode("utf-8", "replace"), ttfb, total)
    finally:
        conn.close()


def run_probe(scenario: Scenario, conn_factory: ConnFactory, host: str, api_key: str, quota: bool) -> bool:
    """Runs one probe, prints its line and returns whether it passed."""
    num = scenario.probe
    if scenario.destructive and not quota:
        print(
            f"  ✅ Probe {num}: {scenario.name} not run (destructive; set ESMERALDA_TEST_QUOTA_EXHAUSTION=1 to run it)",
            flush=True,
        )
        return True
    user_id = "partner-smoke-test"
    result: Optional[Result] = None
    problem: Optional[str] = "no attempt"
    try:
        for _ in range(max(1, scenario.attempts)):
            result = send(scenario, conn_factory, host, api_key, user_id)
            problem = scenario.check(result.status, result.text)
            if problem is None:
                break
    except (OSError, http.client.HTTPException) as exc:
        problem = f"{type(exc).__name__}: {exc}"
    if problem is None and result is not None and scenario.stream:
        if not result.text.strip():
            problem = "empty SSE stream"
        elif result.ttfb is None or result.ttfb <= 0 or result.ttfb > result.total:
            problem = f"invalid TTFB metrics (ttfb={result.ttfb}, total={result.total:.3f}s)"
    if problem is not None:
        detail = f"{scenario.label or scenario.name}: {problem}"
        if result is not None and result.text and scenario.attempts == 1:
            detail += f" | {' '.join(result.text.split())[:EXCERPT]}"
        print(f"  ❌ Probe {num}: {detail}", flush=True)
        return False
    assert result is not None
    if scenario.stream:
        detail = f"{scenario.label} (TTFB={result.ttfb:.3f}s, total={result.total:.3f}s)"
    else:
        detail = f"{scenario.label} (HTTP {result.status})"
    print(f"  ✅ Probe {num}: {detail}", flush=True)
    return True


def main(env: Optional[Dict[str, str]] = None) -> int:
    env = dict(os.environ if env is None else env)
    missing = [k for k in ("PARTNER_HOST", "PARTNER_ENTRY_IP", "PARTNER_API_KEY", "PARTNER_CA_FILE") if not env.get(k)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}", flush=True)
        return 2
    host, ip = env["PARTNER_HOST"], env["PARTNER_ENTRY_IP"]
    context = ssl.create_default_context(cafile=env["PARTNER_CA_FILE"])
    quota = env.get("ESMERALDA_TEST_QUOTA_EXHAUSTION", "0") == "1"

    def factory(timeout: float) -> http.client.HTTPConnection:
        return ResolvedHTTPSConnection(host, ip, context, timeout)

    results = [run_probe(s, factory, host, env["PARTNER_API_KEY"], quota) for s in probes()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
