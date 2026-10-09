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

"""Telemetry: structured JSON events for Cloud Logging, and caller attributes on spans.

Events are single JSON lines on stdout. Agent Runtime ingests them as ``jsonPayload``; the
Layer 4 log sinks, log-based metrics and dashboards key on ``jsonPayload.event``
(``genai_token_consumption``, ``mcp_tool_execution``).
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from opentelemetry import baggage, trace
from opentelemetry.sdk.trace import SpanProcessor

from esmeralda.context import BAGGAGE_AGENT_NAME, BAGGAGE_PROJECT_ID


def _trace_ids() -> dict[str, str]:
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        return {"trace_id": format(span_context.trace_id, "032x"), "span_id": format(span_context.span_id, "016x")}
    return {"trace_id": "unknown_trace", "span_id": "unknown_span"}


class TelemetryEmitter:
    """Writes Esmeralda telemetry events as one JSON line each."""

    def __init__(self, stream: TextIO | None = None):
        self._stream = stream

    def emit(self, payload: dict[str, Any]) -> None:
        stream = self._stream or sys.stdout
        stream.write(json.dumps(payload, default=str) + "\n")
        stream.flush()

    def token_consumption(
        self,
        *,
        agent_id: str,
        session_id: str,
        user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        thoughts_tokens: int = 0,
        cached_tokens: int = 0,
        finish_reason: str = "STOP",
        execution_path: str | None = None,
        turn_index: int = 1,
    ) -> None:
        cache_hit_ratio = cached_tokens / prompt_tokens if prompt_tokens > 0 else 0.0
        self.emit(
            {
                "event": "genai_token_consumption",
                "session_id": session_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "execution_path": execution_path or f"{agent_id}@1",
                "turn_index": turn_index,
                **_trace_ids(),
                "model": model,
                "tokens": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "thoughts_tokens": thoughts_tokens,
                    "cached_tokens": cached_tokens,
                    "total_tokens": total_tokens,
                },
                "implicit_caching": {"cache_hit": cached_tokens > 0, "cache_hit_ratio": round(cache_hit_ratio, 4)},
                "finish_reason": finish_reason,
            }
        )

    def tool_execution(
        self,
        *,
        agent_id: str,
        tool_name: str,
        session_id: str,
        user_id: str,
        status: str,
        duration_ms: float,
        error_reason: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "mcp_tool_execution",
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "status": status,
            "duration_ms": round(duration_ms, 2),
            **_trace_ids(),
        }
        if error_reason:
            payload["error_reason"] = error_reason
        self.emit(payload)


class BaggageSpanProcessor(SpanProcessor):
    """Copies the caller context from OpenTelemetry baggage onto every span as attributes.

    That makes caller.project_id / caller.agent_name filterable in Cloud Trace (and any other
    trace backend) on every span of the invocation, including the model and tool spans ADK creates.
    """

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        for key in (BAGGAGE_PROJECT_ID, BAGGAGE_AGENT_NAME):
            value = baggage.get_baggage(key, context=parent_context)
            if value:
                span.set_attribute(key, str(value))
