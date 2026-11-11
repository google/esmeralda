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

* ``Authorization: Bearer <token>``: an ID token for the server's origin, or an access token for
  hosts that take one (cached, see ``esmeralda.auth.token_kind``); skipped for local servers;
* the delegated user credential, only when the server's host is listed in
  ``$ESMERALDA_USER_CREDENTIAL_TARGETS``, in the header configured there (default ``X-User-Token``).
  It is brokered by ``esmeralda.auth_manager`` when Auth Manager is configured, else it is the
  invocation's own token (``esmeralda.context.user_token``). See ``esmeralda.user_credential``;
* ``X-Agent-Spiffe-Id``: the calling agent's attested SPIFFE / ``principal://`` identity, when present;
* ``X-API-Key``: the calling agent's name (the gateway rate-limits per key).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from esmeralda import auth, auth_manager, user_credential

logger = logging.getLogger(__name__)

USER_TOKEN_HEADER = user_credential.DEFAULT_HEADER
"""Default header of the delegated user credential (per host: ``$ESMERALDA_USER_CREDENTIAL_TARGETS``)."""
SPIFFE_ID_HEADER = auth_manager.SPIFFE_ID_HEADER
API_KEY_HEADER = "X-API-Key"


def headers_for(
    url: str,
    ctx: Any = None,
    *,
    api_key: str | None = None,
    token: str | None = None,
    auth_provider: str | None = None,
) -> dict[str, str]:
    """The request headers for one call to the MCP server at ``url``.

    ``token`` (``"id"`` or ``"access"``) overrides ``esmeralda.auth.token_kind(url)``.
    """
    headers = _static_headers(api_key)
    if not auth.is_local(url):
        try:
            headers["Authorization"] = f"Bearer {auth.bearer_token(url, token)}"
        except Exception:
            logger.exception("No bearer token for %s; calling it without one", url)

    if ctx is not None or auth_provider or os.environ.get(auth_manager.AUTH_PROVIDER_ENV):
        headers.update(user_credential.outgoing_headers(url, ctx, auth_provider=auth_provider))

    agent_spiffe = auth_manager.spiffe_id()
    if agent_spiffe:
        headers[SPIFFE_ID_HEADER] = agent_spiffe
    return headers


def toolset(
    url: str,
    *,
    prefix: str,
    api_key: str | None = None,
    auth_provider: str | None = None,
    timeout: float = 30.0,
    sse_read_timeout: float = 300.0,
    token: str | None = None,
    **kwargs: Any,
) -> Any:
    """An ADK ``McpToolset`` for the MCP server at ``url``; tool names get ``<prefix>_``.

    ``token`` (``"id"`` or ``"access"``) overrides the bearer token kind (default
    ``esmeralda.auth.token_kind(url)``).
    """
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            headers=_static_headers(api_key),  # session setup; per-request headers come from header_provider
            timeout=timeout,
            sse_read_timeout=sse_read_timeout,
        ),
        header_provider=lambda ctx: headers_for(
            url, ctx, api_key=api_key, token=token, auth_provider=auth_provider
        ),
        tool_name_prefix=prefix,
        **kwargs,
    )


def _static_headers(api_key: str | None) -> dict[str, str]:
    return {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        API_KEY_HEADER: api_key or os.environ.get("AGENT_NAME") or "esmeralda-agent",
    }
