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
* emits ``esmeralda.usage`` records (``contracts/usage-event.schema.json``): one ``llm.call`` per
  model response (token usage incl. cached and reasoning tokens), one ``tool.call`` per tool call
  (status, duration), and one ``agent.invocation`` per run (latency, token roll-up);
* while Layer 4 still keys on them, also emits the legacy ``genai_token_consumption`` and
  ``mcp_tool_execution`` events (``ESMERALDA_LEGACY_TELEMETRY_EVENTS=false`` turns them off).

``workload`` comes from the environment (``usage.Workload.from_env``, framework ``adk``).
``caller.declared`` comes from the caller context, with ``caller.verified=false``: the caller
context is self-declared, so it is never used as verified attribution.

Telemetry never breaks a request: failures are logged and the run continues.
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from google.adk.plugins.base_plugin import BasePlugin

from esmeralda import context, lifecycle, usage, user_credential
from esmeralda.telemetry import TelemetryEmitter

logger = logging.getLogger(__name__)

# Bounds the per-invocation bookkeeping if a run never reaches after_run (interrupted or aborted).
_MAX_TRACKED = 1024

LEGACY_EVENTS_ENV = "ESMERALDA_LEGACY_TELEMETRY_EVENTS"


@dataclass
class _Run:
    started: float
    caller: usage.Caller | None
    agent_name: str
    conversation_id: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    model_errors: list[str] = field(default_factory=list)


class EsmeraldaTelemetryPlugin(BasePlugin):
    def __init__(
        self,
        *,
        agent_name: str | None = None,
        finalize_steps: tuple[lifecycle.Step, ...] = lifecycle.FINALIZE,
        emitter: TelemetryEmitter | None = None,
        recorder: usage.UsageRecorder | None = None,
        legacy_events: bool | None = None,
    ):
        super().__init__(name="esmeralda_telemetry")
        self._agent_name = agent_name
        self._finalize_steps = finalize_steps
        self._emitter = emitter or TelemetryEmitter()
        self._recorder = recorder or usage.UsageRecorder(transport=self._emitter.transport, workload=self._workload)
        if legacy_events is None:
            legacy_events = os.environ.get(LEGACY_EVENTS_ENV, "true").strip().lower() not in {"0", "false", "no", "off"}
        self._legacy_events = legacy_events
        self._baggage_tokens: OrderedDict[str, object] = OrderedDict()
        self._tool_starts: OrderedDict[str, float] = OrderedDict()
        self._model_starts: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
        self._runs: OrderedDict[str, _Run] = OrderedDict()

    @property
    def agent_name(self) -> str:
        return self._agent_name or os.environ.get("AGENT_NAME") or "unknown_agent"

    def _workload(self) -> usage.Workload:
        """``workload`` of every record: from the environment, framework ``adk``."""
        return usage.Workload.from_env(name=self._agent_name, framework="adk")

    # ---- run lifecycle ------------------------------------------------------------------

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        lifecycle.finalize(self._finalize_steps)
        caller = context.from_invocation(invocation_context)
        if caller:
            _remember(self._baggage_tokens, invocation_context.invocation_id, context.attach_baggage(caller))
        try:
            agent = getattr(invocation_context, "agent", None)
            _remember(
                self._runs,
                invocation_context.invocation_id,
                _Run(
                    started=time.monotonic(),
                    caller=usage.Caller.from_declared(caller.to_dict() if caller else None),
                    agent_name=str(getattr(agent, "name", None) or self.agent_name),
                    conversation_id=_session_id(invocation_context, default=None),
                ),
            )
        except Exception:
            logger.exception("Failed to start invocation telemetry")
        return None

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        try:
            run = self._runs.pop(invocation_context.invocation_id, None)
            if run is not None:
                self._recorder.record_agent_invocation(
                    agent_name=run.agent_name,
                    latency_ms=(time.monotonic() - run.started) * 1000.0,
                    status="error" if run.model_errors else "ok",
                    error_type=run.model_errors[-1] if run.model_errors else None,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    cached_tokens=run.cached_tokens,
                    reasoning_tokens=run.reasoning_tokens,
                    conversation_id=run.conversation_id,
                    caller=run.caller,
                    secrets=_secrets(invocation_context),
                )
        except Exception:
            logger.exception("Failed to emit invocation telemetry")
        finally:
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
        self._emit_tool(tool, tool_context, status="ERROR", error=error)
        return None

    def _emit_tool(self, tool: Any, tool_context: Any, *, status: str, error: Exception | None = None) -> None:
        try:
            started = self._tool_starts.pop(_call_key(tool_context), None)
            duration_ms = (time.monotonic() - started) * 1000.0 if started is not None else 0.0
            tool_name = str(getattr(tool, "name", "unknown_tool"))
            run = self._run_for(tool_context)
            secrets = _secrets(tool_context)
            self._recorder.record_tool_call(
                name=tool_name,
                status=status,
                duration_ms=duration_ms,
                server=_tool_server(tool),
                call_id=getattr(tool_context, "function_call_id", None),
                error_type=type(error).__name__ if error is not None else None,
                agent_name=_agent_name(tool_context, run, self.agent_name),
                conversation_id=_session_id(tool_context, default=None),
                caller=run.caller if run else None,
                secrets=secrets,
            )
            if self._legacy_events:
                self._emitter.tool_execution(
                    agent_id=self.agent_name,
                    tool_name=tool_name,
                    session_id=_session_id(tool_context),
                    user_id=_user_id(tool_context),
                    status=status,
                    duration_ms=duration_ms,
                    error_reason=user_credential.describe_error(error, secrets) if error is not None else None,
                    secrets=secrets,
                )
        except Exception:
            logger.exception("Failed to emit tool telemetry")

    # ---- model ----------------------------------------------------------------------------

    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> None:
        try:
            _remember(
                self._model_starts,
                _model_key(callback_context),
                (time.monotonic(), getattr(llm_request, "model", None)),
            )
        except Exception:
            logger.exception("Failed to start model telemetry")
        return None

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> None:
        try:
            usage_metadata = getattr(llm_response, "usage_metadata", None)
            if usage_metadata is None or getattr(llm_response, "partial", False):
                return None
            prompt = usage_metadata.prompt_token_count
            completion = usage_metadata.candidates_token_count
            total = usage_metadata.total_token_count
            if prompt is None or completion is None or total is None:
                logger.debug("Model response without complete token counts: %s", usage_metadata)
                return None
            thoughts = getattr(usage_metadata, "thoughts_token_count", None) or 0
            cached = getattr(usage_metadata, "cached_content_token_count", None) or 0
            tool_prompt = getattr(usage_metadata, "tool_use_prompt_token_count", None) or 0
            started, request_model = self._model_starts.pop(_model_key(callback_context), (None, None))
            response_model = getattr(llm_response, "model_version", None)
            finish_reason = getattr(llm_response, "finish_reason", None)
            finish = getattr(finish_reason, "name", None) or (str(finish_reason) if finish_reason else None)
            # OpenTelemetry GenAI semantics: input includes cached and tool-use prompt tokens,
            # output includes reasoning tokens.
            input_tokens, output_tokens = prompt + tool_prompt, completion + thoughts
            run = self._run_for(callback_context)
            secrets = _secrets(callback_context)
            if run is not None:
                run.input_tokens += input_tokens
                run.output_tokens += output_tokens
                run.cached_tokens += cached
                run.reasoning_tokens += thoughts
            self._recorder.record_llm_call(
                request_model=request_model or (None if response_model else os.environ.get("MODEL_NAME")),
                response_model=response_model,
                system=_gen_ai_system(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached,
                reasoning_tokens=thoughts,
                finish_reasons=[finish.lower()] if finish else None,
                agent_name=_agent_name(callback_context, run, self.agent_name),
                conversation_id=_session_id(callback_context, default=None),
                latency_ms=(time.monotonic() - started) * 1000.0 if started is not None else None,
                caller=run.caller if run else None,
                secrets=secrets,
            )
            if self._legacy_events:
                self._emitter.token_consumption(
                    agent_id=self.agent_name,
                    session_id=_session_id(callback_context),
                    user_id=_user_id(callback_context),
                    model=response_model or os.environ.get("MODEL_NAME", "unknown"),
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=total,
                    thoughts_tokens=thoughts,
                    cached_tokens=cached,
                    finish_reason=finish or "STOP",
                    secrets=secrets,
                )
        except Exception:
            logger.exception("Failed to emit model telemetry")
        return None

    async def on_model_error_callback(self, *, callback_context: Any, llm_request: Any, error: Exception) -> None:
        try:
            started, request_model = self._model_starts.pop(_model_key(callback_context), (None, None))
            run = self._run_for(callback_context)
            if run is not None:
                run.model_errors.append(type(error).__name__)
            self._recorder.record_llm_call(
                request_model=request_model or getattr(llm_request, "model", None) or os.environ.get("MODEL_NAME"),
                system=_gen_ai_system(),
                agent_name=_agent_name(callback_context, run, self.agent_name),
                conversation_id=_session_id(callback_context, default=None),
                status="error",
                error_type=type(error).__name__,
                latency_ms=(time.monotonic() - started) * 1000.0 if started is not None else None,
                caller=run.caller if run else None,
                secrets=_secrets(callback_context),
            )
        except Exception:
            logger.exception("Failed to emit model error telemetry")
        return None

    def _run_for(self, ctx: Any) -> _Run | None:
        invocation_id = getattr(ctx, "invocation_id", None)
        return self._runs.get(invocation_id) if isinstance(invocation_id, str) else None


def _remember(store: OrderedDict, key: str, value: Any) -> None:
    store[key] = value
    while len(store) > _MAX_TRACKED:
        store.popitem(last=False)


def _secrets(ctx: Any) -> tuple[str, ...]:
    """The invocation's delegated user credential, to be masked wherever it might appear."""
    try:
        value = context.user_token(ctx)
    except Exception:
        return ()
    return (value,) if value else ()


def _call_key(tool_context: Any) -> str:
    return getattr(tool_context, "function_call_id", None) or str(id(tool_context))


def _model_key(callback_context: Any) -> str:
    invocation_id = getattr(callback_context, "invocation_id", None)
    agent_name = getattr(callback_context, "agent_name", None)
    if isinstance(invocation_id, str):
        return f"{invocation_id}/{agent_name}"
    return str(id(callback_context))


def _session_id(ctx: Any, default: str | None = "unknown_session") -> str | None:
    session = getattr(ctx, "session", None)
    value = getattr(session, "id", None)
    return str(value) if value else default


def _user_id(ctx: Any) -> str:
    return str(getattr(ctx, "user_id", None) or "anonymous")


def _agent_name(ctx: Any, run: _Run | None, default: str) -> str:
    name = getattr(ctx, "agent_name", None)
    if isinstance(name, str) and name:
        return name
    return run.agent_name if run else default


def _tool_server(tool: Any) -> str | None:
    """Best-effort MCP server name of an ADK tool: an explicit name attribute, else the first
    label of the MCP connection URL host (``income-verification.esmeralda.internal`` →
    ``income-verification``). None for function tools."""
    for attr in ("server_name", "_server_name", "toolset_name"):
        value = getattr(tool, attr, None)
        if isinstance(value, str) and value:
            return value
    try:
        params = getattr(getattr(tool, "_mcp_session_manager", None), "_connection_params", None)
        url = getattr(params, "url", None)
        if isinstance(url, str) and url:
            host = urlsplit(url).hostname or ""
            return host.split(".", 1)[0] or None
    except Exception:
        logger.debug("Could not derive the MCP server of tool %r", getattr(tool, "name", tool))
    return None


def _gen_ai_system() -> str:
    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "true").strip().lower()
    return "gcp.vertex_ai" if use_vertex in {"1", "true", "yes"} else "gcp.gemini"
