# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import os

import google.auth
import google.auth.transport.requests
import httpx
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

logger = logging.getLogger(__name__)

A2A_AGENT_URL = os.getenv("A2A_AGENT_URL")

# Must match a2a-agent/agent.USER_AUTH_TOKEN_KEY
USER_AUTH_TOKEN_KEY = "user_auth_token"


async def _add_auth_header(request):
    """Inject access token for Vertex AI Agent Engine A2A endpoint."""
    loop = asyncio.get_running_loop()
    credentials, _ = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    await loop.run_in_executor(None, credentials.refresh, auth_req)
    request.headers["Authorization"] = f"Bearer {credentials.token}"


def _a2a_metadata_provider(invocation_context, a2a_message):
    """Attach user auth token from session state to A2A request metadata.

    This is called before sending each A2A message. It reads the user token
    from session state (placed there by streaming_agent_run_with_events via
    the authorizations field) and attaches it as A2A metadata so the
    receiving agent can extract it.
    """
    metadata = {}
    if invocation_context.session and invocation_context.session.state:
        token = invocation_context.session.state.get(USER_AUTH_TOKEN_KEY)
        if token:
            metadata[USER_AUTH_TOKEN_KEY] = token
            logger.info("Attaching user auth token to A2A metadata")
    return metadata


_httpx_client = httpx.AsyncClient(
    event_hooks={"request": [_add_auth_header]},
    timeout=httpx.Timeout(60.0),
)

mortgage_tools_agent = RemoteA2aAgent(
    name="mortgage_tools_agent",
    description="Mortgage underwriting assistant with document management, "
                "income verification, and corporate email capabilities. "
                "Delegate all mortgage-related queries to this agent.",
    agent_card=f"{A2A_AGENT_URL}/v1/card",
    httpx_client=_httpx_client,
    a2a_request_meta_provider=_a2a_metadata_provider,
)
