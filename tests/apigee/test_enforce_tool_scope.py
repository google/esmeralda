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

"""Offline unit tests for apps/apigee/proxies/partner-mcp/.../enforce-tool-scope.js."""

from __future__ import annotations

import json
from typing import Any

from test_extract_tokens import REPO_ROOT, _run_apigee_js

ENFORCE_TOOL_SCOPE_JS = (
    REPO_ROOT
    / "apps"
    / "apigee"
    / "proxies"
    / "partner-mcp"
    / "apiproxy"
    / "resources"
    / "jsc"
    / "enforce-tool-scope.js"
)

# kvm-partner-mcp entries written by the partner-mcp capability module.
ALLOWED_SERVICES = "corporate-email,income-verification"
APEX_DOMAIN = "esmeralda.internal"


def _eval_tool_scope(
    rpc_body: dict[str, Any] | None,
    allowed_mcp_tools: str | None,
    path_suffix: str = "/income-verification/mcp",
    allowed_services: str | None = ALLOWED_SERVICES,
    apex_domain: str | None = APEX_DOMAIN,
) -> dict[str, Any]:
    initial_vars: dict[str, Any] = {"proxy.pathsuffix": path_suffix}
    if rpc_body is not None:
        initial_vars["request.content"] = json.dumps(rpc_body)
    if allowed_mcp_tools is not None:
        initial_vars["verifyapikey.VAK-Verify-Partner-Key.apiproduct.allowed_mcp_tools"] = allowed_mcp_tools
    if allowed_services is not None:
        initial_vars["mcp.allowed_services"] = allowed_services
    if apex_domain is not None:
        initial_vars["mcp.apex_domain"] = apex_domain
    return _run_apigee_js(ENFORCE_TOOL_SCOPE_JS, initial_vars)


def _call(name: str, rpc_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call", "params": {"name": name, "arguments": {}}}


def test_allowed_tools_call_passes() -> None:
    """A tools/call for a tool in allowed_mcp_tools is permitted and routed to <service>.<domain>."""
    state = _eval_tool_scope(
        rpc_body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "verify_applicant", "arguments": {"first_name": "Julian", "last_name": "Sterling"}},
        },
        allowed_mcp_tools="verify_applicant, read_email",
        path_suffix="/income-verification/mcp",
    )
    assert state["mcp.service.allowed"] is True
    assert state["mcp.tool.forbidden"] is False
    assert state["mcp.tool.name"] == "verify_applicant"
    assert state["mcp.service.host"] == "income-verification"
    assert state["mcp.target.host"] == "income-verification.esmeralda.internal"
    assert state["mcp.target.audience"] == "https://income-verification.esmeralda.internal"


def test_disallowed_tools_call_rejected() -> None:
    """A tools/call for an unallowlisted tool sets mcp.tool.forbidden = true."""
    state = _eval_tool_scope(
        rpc_body=_call("restricted_admin_drop_table", 2),
        allowed_mcp_tools="verify_applicant, read_email",
        path_suffix="/corporate-email/mcp",
    )
    assert state["mcp.service.allowed"] is True
    assert state["mcp.tool.forbidden"] is True
    assert state["mcp.tool.name"] == "restricted_admin_drop_table"


def test_service_qualified_tool_scope() -> None:
    """`<service>:<tool>` entries only allow the tool on that service."""
    allowed = "corporate-email:read_email"
    ok = _eval_tool_scope(_call("read_email"), allowed, path_suffix="/corporate-email/mcp")
    assert ok["mcp.tool.forbidden"] is False
    other = _eval_tool_scope(_call("read_email"), allowed, path_suffix="/income-verification/mcp")
    assert other["mcp.tool.forbidden"] is True


def test_tools_list_and_initialize_allowed_even_with_restricted_scope() -> None:
    """Discovery methods (tools/list, initialize) pass without triggering a 403."""
    for method in ("initialize", "tools/list"):
        state = _eval_tool_scope(
            rpc_body={"jsonrpc": "2.0", "id": 3, "method": method, "params": {}},
            allowed_mcp_tools="verify_applicant",
            path_suffix="/income-verification/mcp",
        )
        assert state["mcp.service.allowed"] is True
        assert state["mcp.tool.forbidden"] is False
        assert state["mcp.rpc.method"] == method


def test_wildcard_product_scope_allows_any_tool() -> None:
    """An API product with allowed_mcp_tools = '*' permits any tools/call."""
    state = _eval_tool_scope(
        rpc_body=_call("send_email", 4),
        allowed_mcp_tools="*",
        path_suffix="/corporate-email/mcp",
    )
    assert state["mcp.tool.forbidden"] is False
    assert state["mcp.tool.name"] == "send_email"
    assert state["mcp.target.audience"] == "https://corporate-email.esmeralda.internal"


def test_service_not_exposed_to_partners_is_not_routed() -> None:
    """A service outside allowed_mcp_services (e.g. an internal agent host) is never routed."""
    for suffix in ("/legacy-dms/mcp", "/cx-mortgage-orchestrator/mcp", "/partner/mcp", "/../mcp", "/mcp"):
        state = _eval_tool_scope(_call("verify_applicant"), "*", path_suffix=suffix)
        assert state["mcp.service.allowed"] is False, suffix
        assert state.get("mcp.target.host") is None, suffix
        assert state.get("mcp.target.audience") is None, suffix


def test_no_kvm_allowlist_routes_nothing() -> None:
    """Without the kvm-partner-mcp allowlist, no service is routed (fail closed)."""
    state = _eval_tool_scope(_call("verify_applicant"), "*", allowed_services=None)
    assert state["mcp.service.allowed"] is False


def test_apex_domain_comes_from_kvm() -> None:
    """The southbound host and audience follow the kvm-partner-mcp apex_domain entry."""
    state = _eval_tool_scope({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, "*", apex_domain="corp.example")
    assert state["mcp.target.host"] == "income-verification.corp.example"
    assert state["mcp.target.audience"] == "https://income-verification.corp.example"
