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

"""Single catalog of the Apigee partner front-door scenarios.

Every request a partner can send through ``https://partner.<domain>`` is defined here once and
used by:

* ``tests/partner/probe.py`` (the live conformance probes behind ``scripts/test_partner.sh`` and
  ``esmeralda verify --partner``): every scenario with ``probe`` set, in probe order;
* ``tests/load_test/partner_load_test.py`` (``make load-test-partner``): every scenario with
  ``weight > 0``, as a weighted Locust task (destructive ones only with ``--include-quota``).

Adding a probe = appending one ``Scenario`` below. Keep this file stdlib-only and Python 3.9
compatible: it is copied to the test VM, which only has the distribution's ``python3``.

Placeholders in ``path``, ``headers`` and ``body`` strings are filled per request by ``render``:
``{user_id}`` (a synthetic per-user partner end-user id) and ``{request_id}``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

# Auth modes: how the partner API key header is sent.
AUTH_KEY = "key"  # the partner consumer key (x-api-key)
AUTH_NONE = "none"  # no x-api-key header at all
AUTH_INVALID = "invalid"  # a syntactically valid but unknown key
AUTH_MODES = (AUTH_KEY, AUTH_NONE, AUTH_INVALID)

API_KEY_HEADER = "x-api-key"
INVALID_API_KEY = "esmeralda-invalid-partner-key-000000"

AGENTS_BASE = "/partner/v1/agents"
MCP_BASE = "/partner/v1/mcp"
ORCHESTRATOR = "cx-mortgage-orchestrator"
STREAM_PATH = f"{AGENTS_BASE}/{ORCHESTRATOR}:streamQuery?alt=sse"

JSON = {"Content-Type": "application/json"}
SSE = {"Content-Type": "application/json", "Accept": "text/event-stream"}
# Streamable HTTP MCP clients must accept both (the servers answer JSON in stateless mode).
MCP = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@dataclass(frozen=True)
class Scenario:
    """One partner request and what the front door must answer."""

    name: str
    description: str
    proxy: str  # Apigee proxy that serves it (Analytics groups by this)
    method: str
    path: str
    expect: FrozenSet[int]
    auth: str = AUTH_KEY
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    contains: Optional[str] = None  # case-insensitive substring the response body must contain
    stream: bool = False  # SSE response: measure TTFB and total
    destructive: bool = False  # burns shared quota: opt-in only, never in the default load mix
    weight: int = 1  # Locust task weight; 0 keeps it out of the load test
    timeout: float = 15.0  # seconds for the whole request
    attempts: int = 1  # >1: repeat until a status in ``expect`` is seen (quota exhaustion)
    probe: Optional[int] = None  # conformance probe number in `esmeralda verify --partner`
    label: str = ""  # short success text printed by the conformance probe

    def render(self, user_id: str, request_id: str = "") -> Tuple[str, Dict[str, str], Optional[bytes]]:
        """Path, headers (without the API key) and encoded body for one request."""
        values = {"user_id": user_id, "request_id": request_id}
        headers = {k: _fill(v, values) for k, v in self.headers.items()}
        body = None
        if self.body is not None:
            body = json.dumps(_fill(copy.deepcopy(self.body), values)).encode()
        return _fill(self.path, values), headers, body

    def auth_headers(self, api_key: str) -> Dict[str, str]:
        """The API key header for this scenario's auth mode."""
        if self.auth == AUTH_KEY:
            return {API_KEY_HEADER: api_key}
        if self.auth == AUTH_INVALID:
            return {API_KEY_HEADER: INVALID_API_KEY}
        return {}

    def check(self, status: int, text: str) -> Optional[str]:
        """None when the response meets the expectation, else a short reason (never echoes headers)."""
        if status not in self.expect:
            return f"HTTP {status}, expected {'/'.join(str(c) for c in sorted(self.expect))}"
        if self.contains and status < 300 and self.contains.lower() not in (text or "").lower():
            return f"HTTP {status} without '{self.contains}' in the response"
        return None


def _fill(value: Any, values: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for key, sub in values.items():
            value = value.replace("{" + key + "}", sub)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    return value


def _orchestrator(name: str, message: str, *, weight: int, probe: Optional[int] = None, label: str = "") -> Scenario:
    return Scenario(
        name=name,
        description=f"Partner key -> {ORCHESTRATOR} :streamQuery (SSE): {message}",
        proxy="partner-agents",
        method="POST",
        path=STREAM_PATH,
        expect=frozenset({200}),
        headers=SSE,
        body={"class_method": "async_stream_query", "input": {"message": message, "user_id": "{user_id}"}},
        contains="Sterling",
        stream=True,
        weight=weight,
        timeout=280.0,  # the Apigee SSE target allows 300 s
        probe=probe,
        label=label,
    )


def _mcp_rpc(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rpc: Dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        rpc["params"] = params
    return rpc


SCENARIOS: Tuple[Scenario, ...] = (
    # --- partner-agents: authentication -------------------------------------------------------
    Scenario(
        name="agents-no-key",
        description="No x-api-key on the orchestrator card -> 401 (VerifyAPIKey)",
        proxy="partner-agents",
        method="GET",
        path=f"{AGENTS_BASE}/{ORCHESTRATOR}/v1/card",
        expect=frozenset({401}),
        auth=AUTH_NONE,
        weight=2,
        probe=1,
        label="Unauthenticated request rejected",
    ),
    Scenario(
        name="agents-invalid-key",
        description="Unknown x-api-key on the orchestrator card -> 401 (VerifyAPIKey)",
        proxy="partner-agents",
        method="GET",
        path=f"{AGENTS_BASE}/{ORCHESTRATOR}/v1/card",
        expect=frozenset({401}),
        auth=AUTH_INVALID,
        weight=2,
    ),
    # --- partner-agents: agent allowlist -----------------------------------------------------
    Scenario(
        name="agents-specialist-card-blocked",
        description="Partner key -> internal specialist card -> 403/404 (RF-Agent-Not-Allowed)",
        proxy="partner-agents",
        method="GET",
        path=f"{AGENTS_BASE}/ai-coe-mortgage-specialist/v1/card",
        expect=frozenset({403, 404}),
        weight=2,
        probe=2,
        label="Internal specialist ai-coe-mortgage-specialist blocked",
    ),
    Scenario(
        name="agents-specialist-stream-blocked",
        description="Partner key -> internal specialist :streamQuery -> 403/404 before any model call",
        proxy="partner-agents",
        method="POST",
        path=f"{AGENTS_BASE}/ai-coe-mortgage-specialist:streamQuery?alt=sse",
        expect=frozenset({403, 404}),
        headers=SSE,
        body={"class_method": "async_stream_query", "input": {"message": "Hello", "user_id": "{user_id}"}},
        weight=1,
    ),
    # --- partner-mcp: tool scope ---------------------------------------------------------------
    Scenario(
        name="mcp-restricted-tool-call",
        description="Partner key -> MCP tools/call of a tool outside allowed_mcp_tools -> 403 (RF-Tool-Forbidden)",
        proxy="partner-mcp",
        method="POST",
        path=f"{MCP_BASE}/income-verification/mcp",
        expect=frozenset({403}),
        headers=MCP,
        body=_mcp_rpc("tools/call", {"name": "restricted_admin_drop_table", "arguments": {}}),
        weight=2,
        probe=3,
        label="Unallowlisted MCP tool call rejected",
    ),
    Scenario(
        name="mcp-no-key",
        description="No x-api-key on the MCP endpoint -> 401 (VerifyAPIKey)",
        proxy="partner-mcp",
        method="POST",
        path=f"{MCP_BASE}/income-verification/mcp",
        expect=frozenset({401}),
        auth=AUTH_NONE,
        headers=MCP,
        body=_mcp_rpc("tools/list"),
        weight=1,
    ),
    Scenario(
        name="mcp-tools-list",
        description="Partner key -> MCP tools/list (discovery is allowed by JS-Enforce-Tool-Scope) -> 200",
        proxy="partner-mcp",
        method="POST",
        path=f"{MCP_BASE}/income-verification/mcp",
        expect=frozenset({200}),
        headers=MCP,
        body=_mcp_rpc("tools/list"),
        contains="verify_applicant",
        # Out of the default mix for now: requests the partner-mcp proxy lets through currently
        # get HTTP 404 from its target, so this would only add failures to a demo run.
        weight=0,
    ),
    # --- partner-agents: real orchestrator answers (SSE) ---------------------------------------
    _orchestrator(
        "orchestrator-income",
        "Can you verify Julian Sterling's income?",
        weight=3,
        probe=4,
        label="Orchestrator SSE stream answered",
    ),
    _orchestrator("orchestrator-employment", "What is Julian Sterling's employer and employment status?", weight=1),
    _orchestrator("orchestrator-household", "Summarize the combined household income of Julian and Elena Sterling.", weight=1),
    # --- partner-agents: token quota (destructive, opt-in) -------------------------------------
    Scenario(
        name="orchestrator-quota-exhaustion",
        description="Repeated orchestrator calls until LLMTokenQuota rejects with 429 (burns the product quota)",
        proxy="partner-agents",
        method="POST",
        path=STREAM_PATH,
        expect=frozenset({429}),
        headers=JSON,
        body={"class_method": "async_stream_query", "input": {"message": "Quota probe", "user_id": "{user_id}"}},
        destructive=True,
        weight=1,
        timeout=60.0,
        attempts=25,
        probe=5,
        label="Quota exhaustion rejected",
    ),
)


def by_name(name: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise KeyError(name)


def probes() -> Tuple[Scenario, ...]:
    """Conformance probes, in probe order."""
    return tuple(sorted((s for s in SCENARIOS if s.probe is not None), key=lambda s: s.probe or 0))


def load_mix(include_destructive: bool = False) -> Tuple[Scenario, ...]:
    """Scenarios of the load test (weight > 0), destructive ones only when asked for."""
    return tuple(s for s in SCENARIOS if s.weight > 0 and (include_destructive or not s.destructive))
