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


def _eval_tool_scope(
    rpc_body: dict[str, Any],
    allowed_mcp_tools: str | None,
    path_suffix: str = "/income-verification/mcp",
) -> dict[str, Any]:
    initial_vars: dict[str, Any] = {
        "proxy.pathsuffix": path_suffix,
        "request.content": json.dumps(rpc_body),
    }
    if allowed_mcp_tools is not None:
        initial_vars["verifyapikey.VAK-Verify-Partner-Key.apiproduct.allowed_mcp_tools"] = allowed_mcp_tools
    return _run_apigee_js(ENFORCE_TOOL_SCOPE_JS, initial_vars)


def test_allowed_tools_call_passes() -> None:
    """Verifies a tools/call request for a tool in allowed_mcp_tools is permitted."""
    state = _eval_tool_scope(
        rpc_body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "verify_income", "arguments": {"applicant_name": "Julian Sterling"}},
        },
        allowed_mcp_tools="verify_income, get_employment_status",
        path_suffix="/income-verification/mcp",
    )
    assert state["mcp.tool.forbidden"] is False
    assert state["mcp.tool.name"] == "verify_income"
    assert state["mcp.service.host"] == "income-verification"
    assert state["target.audience"] == "https://income-verification.esmeralda.internal"


def test_disallowed_tools_call_rejected() -> None:
    """Verifies a tools/call request for an unallowlisted tool sets mcp.tool.forbidden = true."""
    state = _eval_tool_scope(
        rpc_body={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "purge_archive_records", "arguments": {}},
        },
        allowed_mcp_tools="verify_income, get_employment_status",
        path_suffix="/legacy-dms/mcp",
    )
    assert state["mcp.tool.forbidden"] is True
    assert state["mcp.tool.name"] == "purge_archive_records"


def test_tools_list_and_initialize_allowed_even_with_restricted_scope() -> None:
    """Verifies discovery methods (tools/list, initialize) pass without triggering a 403."""
    for method in ("initialize", "tools/list"):
        state = _eval_tool_scope(
            rpc_body={"jsonrpc": "2.0", "id": 3, "method": method, "params": {}},
            allowed_mcp_tools="verify_income",
            path_suffix="/income-verification/mcp",
        )
        assert state["mcp.tool.forbidden"] is False
        assert state["mcp.rpc.method"] == method


def test_wildcard_product_scope_allows_any_tool() -> None:
    """Verifies an API product with allowed_mcp_tools = '*' permits any tools/call."""
    state = _eval_tool_scope(
        rpc_body={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "send_preapproval_email", "arguments": {}},
        },
        allowed_mcp_tools="*",
        path_suffix="/corporate-email/mcp",
    )
    assert state["mcp.tool.forbidden"] is False
    assert state["mcp.tool.name"] == "send_preapproval_email"
    assert state["target.audience"] == "https://corporate-email.esmeralda.internal"
