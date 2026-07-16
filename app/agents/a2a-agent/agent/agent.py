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
from typing import Any, Optional

import httpx
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import Agent
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from agent import USER_AUTH_TOKEN_KEY
from agent.prompt import MORTGAGE_ASSISTANT_INSTRUCTION
from agent.tools import create_mcp_toolsets

logger = logging.getLogger(__name__)


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


async def _extract_user_token(callback_context: CallbackContext) -> Optional[types.Content]:
    """Extract user auth token from A2A metadata into session state.

    The A2A executor places incoming message metadata in
    RunConfig.custom_metadata['a2a_metadata']. This callback bridges it
    to session state so the MCP header_provider can read it.
    """
    run_config = callback_context._invocation_context.run_config
    if not run_config or not run_config.custom_metadata:
        return None

    a2a_metadata = run_config.custom_metadata.get("a2a_metadata", {})
    token = a2a_metadata.get(USER_AUTH_TOKEN_KEY)
    if token:
        callback_context.state[USER_AUTH_TOKEN_KEY] = token
        logger.info("User auth token extracted from A2A metadata into session state")

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
    dms_toolset, income_toolset, email_toolset = create_mcp_toolsets()

    _tools = [
        dms_toolset,
        income_toolset,
        email_toolset,
    ]

    return _PickleSafeAgent(
        model=os.environ.get("MODEL_NAME", "gemini-3.5-flash"),
        name=os.environ.get("AGENT_NAME", "a2a_mortgage_agent").replace("-", "_"),
        description=(
            "A mortgage underwriting assistant that connects to legacy document management, "
            "income verification, and corporate email systems through an Agent Gateway."
        ),
        instruction=MORTGAGE_ASSISTANT_INSTRUCTION,
        tools=_tools,
        before_agent_callback=_extract_user_token,
        on_tool_error_callback=_handle_tool_error,
    )


mortgage_assistant_agent = _build_agent()
