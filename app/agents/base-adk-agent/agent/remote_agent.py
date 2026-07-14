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


def _get_id_token(audience: str) -> str:
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport.requests import Request as GoogleAuthRequest
        auth_req = GoogleAuthRequest()
        return google_id_token.fetch_id_token(auth_req, audience)
    except Exception as e:
        logger.warning("Failed to fetch ID token via google.oauth2: %s", e)
        return ""


async def _add_auth_header(request):
    """Inject OIDC ID token for Cloud Run Kong Gateway or access token."""
    loop = asyncio.get_running_loop()
    url = str(request.url)
    if "esmeralda.internal" in url:
        audience = "http://a2a-mortgage-agent.esmeralda.internal"
        id_token = await loop.run_in_executor(None, _get_id_token, audience)
        if id_token:
            request.headers["Authorization"] = f"Bearer {id_token}"
            return
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


# Check if running in Local Sandbox (Mock Router) mode
LOCAL_MODE = os.getenv("LOCAL_MODE") == "true"

if LOCAL_MODE:
    logger.info("🔌 LOCAL_MODE is active! Loading mortgage_assistant_agent in-memory to mock remote agent.")
    try:
        import sys
        # Dynamic path resolution to load the a2a-agent code
        current_dir = os.path.dirname(os.path.abspath(__file__))
        a2a_path = os.path.abspath(os.path.join(current_dir, "../../a2a-agent"))
        
        # Save any existing 'agent' related modules to avoid conflicts
        saved_modules = {}
        for mod_name in list(sys.modules.keys()):
            if mod_name == "agent" or mod_name.startswith("agent."):
                saved_modules[mod_name] = sys.modules.pop(mod_name)
                
        # Insert a2a_path to sys.path
        original_sys_path = list(sys.path)
        if a2a_path not in sys.path:
            sys.path.insert(0, a2a_path)
            
        try:
            from agent.agent import mortgage_assistant_agent
        finally:
            # Restore original sys.path
            sys.path = original_sys_path
            # Restore saved modules to sys.modules
            for mod_name, mod_obj in saved_modules.items():
                sys.modules[mod_name] = mod_obj
                
        # Align the name with what the base coordinator agent expects for delegation
        mortgage_assistant_agent.name = "mortgage_tools_agent"
        mortgage_tools_agent = mortgage_assistant_agent
    except Exception as e:
        logger.error(f"Failed to import local a2a-agent in-memory. Falling back to remote mode: {e}", exc_info=True)
        LOCAL_MODE = False

class CustomRemoteA2aAgent(RemoteA2aAgent):
    """A subclass of RemoteA2aAgent that supports serialization by omitting and
    reconstructing the unpickleable httpx client.
    """
    def __getstate__(self):
        state = self.__dict__.copy()
        state["_httpx_client"] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        if not self._httpx_client and not os.getenv("LOCAL_MODE") == "true":
            self._httpx_client = httpx.AsyncClient(
                event_hooks={"request": [_add_auth_header]},
                timeout=httpx.Timeout(60.0),
            )

    async def _ensure_resolved(self) -> None:
        await super()._ensure_resolved()
        if self._agent_card and A2A_AGENT_URL:
            # Rewrite URL to JSON-RPC endpoint on Vertex AI Reasoning Engine gateway
            logger.info("Rewriting remote A2A agent card URL to use Vertex AI gateway: %s/a2a/agent", A2A_AGENT_URL)
            self._agent_card.url = f"{A2A_AGENT_URL}/a2a/agent"
            if self._agent_card.additional_interfaces:
                for interface in self._agent_card.additional_interfaces:
                    if interface.transport == "jsonrpc":
                        interface.url = f"{A2A_AGENT_URL}/a2a/agent"
            # Re-create the client with the updated agent card URL
            if self._a2a_client_factory:
                self._a2a_client = self._a2a_client_factory.create(self._agent_card)



if not LOCAL_MODE:
    _httpx_client = httpx.AsyncClient(
        event_hooks={"request": [_add_auth_header]},
        timeout=httpx.Timeout(60.0),
    )

    mortgage_tools_agent = CustomRemoteA2aAgent(
        name="mortgage_tools_agent",
        description="Mortgage underwriting assistant with document management, "
                    "income verification, and corporate email capabilities. "
                    "Delegate all mortgage-related queries to this agent.",
        agent_card=f"{A2A_AGENT_URL}/v1/card",
        httpx_client=_httpx_client,
        a2a_request_meta_provider=_a2a_metadata_provider,
    )

