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

"""MCP tool servers behind the gateway, as ADK toolsets.

``toolset(url, prefix=...)`` returns an ``McpToolset`` (streamable HTTP) whose every request carries:

* ``Authorization: Bearer <ID token>`` for the server's origin (cached, see ``esmeralda.auth``);
  skipped for local servers;
* ``User-Auth-Token``: the end user's token for this invocation, when there is one
  (``esmeralda.context.user_token``);
* ``X-API-Key``: the calling agent's name (the gateway rate-limits per key).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from esmeralda import auth, context

logger = logging.getLogger(__name__)

USER_TOKEN_HEADER = "User-Auth-Token"
API_KEY_HEADER = "X-API-Key"


def headers_for(url: str, ctx: Any = None, *, api_key: str | None = None) -> dict[str, str]:
    """The request headers for one call to the MCP server at ``url``."""
    headers = _static_headers(api_key)
    if not auth.is_local(url):
        try:
            headers["Authorization"] = f"Bearer {auth.id_token(auth.audience_for(url))}"
        except Exception:
            logger.exception("No ID token for %s; calling it without one", url)
    token = context.user_token(ctx) if ctx is not None else None
    if token:
        headers[USER_TOKEN_HEADER] = token
    return headers


def toolset(
    url: str,
    *,
    prefix: str,
    api_key: str | None = None,
    timeout: float = 30.0,
    sse_read_timeout: float = 300.0,
    **kwargs: Any,
) -> Any:
    """An ADK ``McpToolset`` for the MCP server at ``url``; tool names get ``<prefix>_``."""
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            headers=_static_headers(api_key),  # session setup; per-request headers come from header_provider
            timeout=timeout,
            sse_read_timeout=sse_read_timeout,
        ),
        header_provider=lambda ctx: headers_for(url, ctx, api_key=api_key),
        tool_name_prefix=prefix,
        **kwargs,
    )


def _static_headers(api_key: str | None) -> dict[str, str]:
    return {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        API_KEY_HEADER: api_key or os.environ.get("AGENT_NAME") or "esmeralda-agent",
    }
