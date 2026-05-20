# Copyright 2025 Google LLC
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

"""ADK agent definition for the mortgage assistant with MCP tool connections."""

import logging
import os
from typing import Any

import httpx
from google.adk.agents.llm_agent import Agent
from google.adk.tools import McpToolset
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.tool_context import ToolContext

from . import tools

logger = logging.getLogger(__name__)

if os.environ.get("DISABLE_SSL_VERIFICATION") == "true":
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    logger.warning("SSL certificate verification has been disabled via environment variable.")

_INSTRUCTION = """You are a mortgage underwriting assistant. You help loan officers process
mortgage applications by retrieving documents, verifying income, and communicating results.

You connect to three backend systems through an Agent Gateway. Each system's tools are
identified by a prefix:

**Document Management (dms_*):**
Tools prefixed with `dms_` connect to the legacy document management system.
Use these to fetch tax returns, pay stubs, bank statements, and other applicant documents.

**Income Verification (income_*):**
Tools prefixed with `income_` connect to a third-party income verification vendor.
Use these to verify reported income against employer records and tax filings.

**Corporate Email (email_*):**
Tools prefixed with `email_` connect to the corporate communications system.
Use `email_read_email` to read the corporate inbox (read-only).
Note: Write operations like sending emails may be restricted by the authorization gateway.

**Workflow:**
1. Fetch the applicant's tax documents using the document management tools.
2. Verify the applicant's reported income using the income verification tools.
3. Compare the figures from both sources and note any discrepancies.
4. Summarize your findings clearly for the loan officer.

**Rules:**
- NEVER fabricate or estimate financial figures. Only report data returned by tools.
- Always cite which tool/system provided each piece of data.
- If a tool call fails or returns an error, report the error honestly to the user.
- Be concise and professional in all responses.
- When presenting tax return or applicant data, ALWAYS include the SSN field and display its value
exactly as returned by the tool (e.g. "[US_SOCIAL_SECURITY_NUMBER]"). Never omit SSN fields.

You also have utility tools:
- list_mcp_connections: Shows which MCP servers are currently connected."""


def _find_http_status_error(exc: BaseException, status_code: int) -> bool:
    """Search exception chains and ExceptionGroups for an HTTPStatusError."""
    seen: set[int] = set()
    queue: list[BaseException] = [exc]
    while queue:
        current = queue.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code == status_code:
            return True
        if current.__cause__ is not None:
            queue.append(current.__cause__)
        if current.__context__ is not None:
            queue.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            queue.extend(current.exceptions)
    return False


def _handle_tool_error(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, error: Exception
) -> dict | None:
    """Handle tool errors, returning a friendly message for 403 policy denials."""
    if _find_http_status_error(error, 403):
        logger.warning("Tool %s denied by authorization policy (403)", tool.name)
        return {
            "error": (
                f"The '{tool.name}' tool call was denied by the authorization "
                "gateway. This operation is not permitted by policy."
            ),
        }
    return None


class _PickleSafeAgent(Agent):
    """Agent that rebuilds with MCP tools when unpickled or deep-copied."""

    def __reduce__(self):
        return (_build_agent, ())

    def __deepcopy__(self, memo):
        return _build_agent()


def _build_agent():
    """Build the agent with all tools including MCP connections.

    Called at import time for local dev, and at unpickle time on Agent Engine.
    """
    _tools = [
        # tools.list_mcp_connections,
    ]

    dms_url = os.environ.get("DMS_MCP_URL", tools.DEFAULT_DMS_MCP_URL)
    income_url = os.environ.get("INCOME_VERIFICATION_URL", tools.DEFAULT_INCOME_VERIFICATION_URL)
    email_url = os.environ.get("EMAIL_MCP_URL", tools.DEFAULT_EMAIL_MCP_URL)

    import httpx
    import urllib.request
    import json
    import google.auth
    import google.auth.transport.requests
    from google.adk.tools.mcp_tool.mcp_session_manager import create_mcp_http_client

    class GoogleOidcAuth(httpx.Auth):
        def __init__(self, audience: str):
            self.audience = audience
            self.creds, self.project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self.req = google.auth.transport.requests.Request()

        def auth_flow(self, request: httpx.Request):
            self.creds.refresh(self.req)
            access_token = self.creds.token
            
            # Fetch SA email. Fallback if not directly available on credentials
            sa_email = getattr(self.creds, "service_account_email", None)
            if not sa_email:
                # If we're on Agent Engine, effective_identity might be available in env
                # or we can just try to fetch the token using the default '-'
                sa_email = "-"
                
            url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateIdToken"
            req_body = json.dumps({"audience": self.audience, "includeEmail": True}).encode("utf-8")
            r = urllib.request.Request(url, data=req_body, headers={
                "Authorization": f"Bearer {access_token}", 
                "Content-Type": "application/json"
            })
            
            try:
                with urllib.request.urlopen(r) as response:
                    token = json.loads(response.read().decode())["token"]
            except Exception as e:
                logger.error(f"Failed to fetch OIDC token via IAM API: {e}")
                raise

            request.headers["Authorization"] = f"Bearer {token}"
            yield request

    def make_dms_client(**kwargs):
        kwargs["auth"] = GoogleOidcAuth(dms_url.replace("/mcp", ""))
        return create_mcp_http_client(**kwargs)

    def make_income_client(**kwargs):
        kwargs["auth"] = GoogleOidcAuth(income_url.replace("/mcp", ""))
        return create_mcp_http_client(**kwargs)

    def make_email_client(**kwargs):
        kwargs["auth"] = GoogleOidcAuth(email_url.replace("/mcp", ""))
        return create_mcp_http_client(**kwargs)

    _tools.insert(
        2,
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=dms_url,
                httpx_client_factory=make_dms_client
            ),
        ),
    )
    _tools.insert(
        3,
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=income_url,
                httpx_client_factory=make_income_client
            ),
        ),
    )
    _tools.insert(
        4,
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=email_url,
                httpx_client_factory=make_email_client
            ),
        ),
    )

    return _PickleSafeAgent(
        model=os.environ.get("MODEL_NAME", "gemini-3.1-flash-lite-preview"),
        name="mortgage_assistant_agent",
        description=(
            "A mortgage underwriting assistant that connects to legacy document management, "
            "income verification, and corporate email systems through an Agent Gateway."
        ),
        instruction=_INSTRUCTION,
        tools=_tools,
        on_tool_error_callback=_handle_tool_error,
    )


root_agent = _build_agent()
