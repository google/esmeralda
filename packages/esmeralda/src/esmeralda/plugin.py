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

"""EsmeraldaTelemetryPlugin: the per-invocation hooks every Esmeralda agent runs.

It is an ADK plugin, so it runs inside the ADK runner and covers every way the agent is served:
the Agent Runtime query and streaming APIs (``adk api_server``), Gemini Enterprise, A2A servers
and ``adk web``.

Per invocation it:

* runs ``lifecycle.finalize()`` (a no-op after the first time);
* puts the caller context in OpenTelemetry baggage for the duration of the run;
* emits ``mcp_tool_execution`` and ``genai_token_consumption`` telemetry events.

Telemetry never breaks a request: failures are logged and the run continues.
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from esmeralda import context, lifecycle
from esmeralda.telemetry import TelemetryEmitter

logger = logging.getLogger(__name__)

# Bounds the per-invocation bookkeeping if a run never reaches after_run (interrupted or aborted).
_MAX_TRACKED = 1024


class EsmeraldaTelemetryPlugin(BasePlugin):
    def __init__(
        self,
        *,
        agent_name: str | None = None,
        finalize_steps: tuple[lifecycle.Step, ...] = lifecycle.FINALIZE,
        emitter: TelemetryEmitter | None = None,
    ):
        super().__init__(name="esmeralda_telemetry")
        self._agent_name = agent_name
        self._finalize_steps = finalize_steps
        self._emitter = emitter or TelemetryEmitter()
        self._baggage_tokens: OrderedDict[str, object] = OrderedDict()
        self._tool_starts: OrderedDict[str, float] = OrderedDict()

    @property
    def agent_name(self) -> str:
        return self._agent_name or os.environ.get("AGENT_NAME") or "unknown_agent"

    # ---- run lifecycle ------------------------------------------------------------------

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        lifecycle.finalize(self._finalize_steps)
        caller = context.from_invocation(invocation_context)
        if caller:
            _remember(self._baggage_tokens, invocation_context.invocation_id, context.attach_baggage(caller))
        return None

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        context.detach_baggage(self._baggage_tokens.pop(invocation_context.invocation_id, None))

    # ---- tools ----------------------------------------------------------------------------

    async def before_tool_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any) -> None:
        _remember(self._tool_starts, _call_key(tool_context), time.monotonic())
        return None

    async def after_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, result: Any
    ) -> None:
        self._emit_tool(tool, tool_context, status="SUCCESS")
        return None

    async def on_tool_error_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any, error: Exception
    ) -> None:
        self._emit_tool(tool, tool_context, status="ERROR", error_reason=f"{type(error).__name__}: {error}")
        return None

    def _emit_tool(self, tool: Any, tool_context: Any, *, status: str, error_reason: str | None = None) -> None:
        try:
            started = self._tool_starts.pop(_call_key(tool_context), None)
            self._emitter.tool_execution(
                agent_id=self.agent_name,
                tool_name=str(getattr(tool, "name", "unknown_tool")),
                session_id=_session_id(tool_context),
                user_id=_user_id(tool_context),
                status=status,
                duration_ms=(time.monotonic() - started) * 1000.0 if started is not None else 0.0,
                error_reason=error_reason,
            )
        except Exception:
            logger.exception("Failed to emit tool telemetry")

    # ---- model ----------------------------------------------------------------------------

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> None:
        try:
            usage = getattr(llm_response, "usage_metadata", None)
            if usage is None or getattr(llm_response, "partial", False):
                return None
            prompt = usage.prompt_token_count
            completion = usage.candidates_token_count
            total = usage.total_token_count
            if prompt is None or completion is None or total is None:
                logger.debug("Model response without complete token counts: %s", usage)
                return None
            finish_reason = getattr(llm_response, "finish_reason", None)
            self._emitter.token_consumption(
                agent_id=self.agent_name,
                session_id=_session_id(callback_context),
                user_id=_user_id(callback_context),
                model=getattr(llm_response, "model_version", None) or os.environ.get("MODEL_NAME", "unknown"),
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                thoughts_tokens=usage.thoughts_token_count or 0,
                cached_tokens=usage.cached_content_token_count or 0,
                finish_reason=getattr(finish_reason, "name", None) or str(finish_reason or "STOP"),
            )
        except Exception:
            logger.exception("Failed to emit model telemetry")
        return None


def _remember(store: OrderedDict, key: str, value: Any) -> None:
    store[key] = value
    while len(store) > _MAX_TRACKED:
        store.popitem(last=False)


def _call_key(tool_context: Any) -> str:
    return getattr(tool_context, "function_call_id", None) or str(id(tool_context))


def _session_id(ctx: Any) -> str:
    session = getattr(ctx, "session", None)
    return str(getattr(session, "id", None) or "unknown_session")


def _user_id(ctx: Any) -> str:
    return str(getattr(ctx, "user_id", None) or "anonymous")
