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
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from agent.prompt import MORTGAGE_ASSISTANT_INSTRUCTION
from agent.tools import dms_toolset, email_toolset, income_toolset

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


_THINKING_LEVELS = {"minimal", "low", "medium", "high"}


def _thinking_config_kwargs(value: str) -> dict[str, Any]:
    """Parse MODEL_THINKING into ThinkingConfig kwargs.

    A level (minimal/low/medium/high) is for Gemini 3 models; an integer is a token budget for
    Gemini 2.5 models. Empty keeps the model default.
    """
    value = value.strip().lower()
    if not value:
        return {}
    if value.lstrip("-").isdigit():
        return {"thinking_budget": int(value)}
    if value not in _THINKING_LEVELS:
        levels = sorted(_THINKING_LEVELS)
        raise ValueError(f"MODEL_THINKING must be one of {levels} or an integer budget, got {value!r}")
    return {"thinking_level": value.upper()}


def _max_output_tokens(value: str) -> int | None:
    """Parse MODEL_MAX_OUTPUT_TOKENS (empty or 0 keeps the model default)."""
    value = value.strip()
    if not value or int(value) <= 0:
        return None
    return int(value)


def _validate_model_settings() -> None:
    model = os.environ.get("MODEL_NAME", "gemini-3.7-flash")
    thinking = _thinking_config_kwargs(os.environ.get("MODEL_THINKING", "low"))
    tokens = _max_output_tokens(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "2048"))
    location = os.environ.get("MODEL_LOCATION", "global")

    if model == "gemini-3.7-flash":
        if thinking.get("thinking_level") not in (None, "LOW", "MEDIUM", "HIGH"):
            raise ValueError(f"{model} does not support MODEL_THINKING={thinking['thinking_level']}")
        if "thinking_budget" in thinking:
            raise ValueError(f"{model} does not support a thinking budget")
        if tokens is not None and tokens > 65536:
            raise ValueError(f"{model} supports at most 65536 output tokens")
        if location not in ("global", "us", "eu"):
            raise ValueError(f"{model} does not support MODEL_LOCATION={location!r}")


def _generate_content_config() -> types.GenerateContentConfig | None:
    """Model settings tuned for latency.

    Almost all of the specialist's latency is its final generation, so the instruction asks for a
    short, fixed-format answer, and:
    - MODEL_THINKING (default "low"): the tool plan is spelled out in the instruction, so the
      model needs little reasoning to follow it.
    - MODEL_MAX_OUTPUT_TOKENS (default 2048): a safety stop well above the ~400-token answer the
      instruction asks for, so a runaway answer is cut instead of running on. Thinking tokens
      count toward it, so it keeps headroom (a cut response loses the answer).
    Temperature stays at the model default (Gemini 3 is tuned for 1.0; lowering it can cause
    looping or worse reasoning).
    """
    kwargs: dict[str, Any] = {}
    thinking = _thinking_config_kwargs(os.environ.get("MODEL_THINKING", "low"))
    if thinking:
        kwargs["thinking_config"] = types.ThinkingConfig(**thinking)
    max_tokens = _max_output_tokens(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "2048"))
    if max_tokens:
        kwargs["max_output_tokens"] = max_tokens
    return types.GenerateContentConfig(**kwargs) if kwargs else None

_validate_model_settings()
mortgage_assistant_agent = Agent(
    model=os.environ.get("MODEL_NAME", "gemini-3.7-flash"),
    name=os.environ.get("AGENT_NAME", "ai_coe_mortgage_specialist").replace("-", "_"),
    description=(
        "A mortgage underwriting assistant that connects to legacy document management, "
        "income verification, and corporate email systems through an Agent Gateway."
    ),
    instruction=MORTGAGE_ASSISTANT_INSTRUCTION,
    tools=[dms_toolset, income_toolset, email_toolset],
    generate_content_config=_generate_content_config(),
    on_tool_error_callback=_handle_tool_error,
)
