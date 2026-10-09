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

"""Calling another agent over A2A, through the gateway.

``remote_agent(name, url=...)`` returns an ADK ``RemoteA2aAgent`` to use as a sub-agent. Every
request carries an ID token for ``url``'s origin (cached; skipped for local URLs) and an
``X-API-Key`` with the calling agent's name. Every message carries A2A metadata with:

* ``caller_context``: this agent's identity, for the callee's telemetry;
* ``user_auth_token``: the end user's token, when the invocation has one.

The agent card is fetched from ``<url>/v1/card``, and its RPC address is pinned to ``url``, so calls
always go through the gateway address configured here, whatever address the card advertises.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from esmeralda import auth, context

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def request_metadata(invocation_context: Any, a2a_message: Any = None) -> dict[str, Any]:
    """A2A request metadata for a call made during ``invocation_context``."""
    metadata: dict[str, Any] = context.outgoing_metadata()
    token = context.user_token(invocation_context)
    if token:
        metadata[context.USER_TOKEN_KEY] = token
    return metadata


def _auth_hook(url: str, api_key: str | None):
    audience = None if auth.is_local(url) else auth.audience_for(url)

    async def add_headers(request: Any) -> None:
        request.headers[API_KEY_HEADER] = api_key or os.environ.get("AGENT_NAME") or "esmeralda-agent"
        if audience:
            request.headers["Authorization"] = f"Bearer {await auth.id_token_async(audience)}"

    return add_headers


def remote_agent(
    name: str,
    *,
    url: str,
    description: str = "",
    api_key: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """An ADK sub-agent that calls the A2A agent served at ``url`` (e.g. its gateway address)."""
    import httpx
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

    base_url = url.rstrip("/")

    class _GatewayRemoteA2aAgent(RemoteA2aAgent):
        def _validate_card_rpc_targets(self, agent_card: Any) -> None:
            # ADK hook (private), run when the card is resolved. The card advertises the agent's own
            # address; pin every RPC address to base_url, then let ADK run its checks (https or
            # loopback, same origin as the card).
            if hasattr(agent_card, "url"):
                agent_card.url = base_url
            for field in ("additional_interfaces", "supported_interfaces"):
                for interface in getattr(agent_card, field, None) or []:
                    if hasattr(interface, "url"):
                        interface.url = base_url
            super()._validate_card_rpc_targets(agent_card)

    client = httpx.AsyncClient(
        event_hooks={"request": [_auth_hook(base_url, api_key)]},
        timeout=httpx.Timeout(timeout),
    )
    return _GatewayRemoteA2aAgent(
        name=name,
        description=description,
        agent_card=f"{base_url}/v1/card",
        httpx_client=client,
        a2a_request_meta_provider=request_metadata,
        use_legacy=False,
    )
