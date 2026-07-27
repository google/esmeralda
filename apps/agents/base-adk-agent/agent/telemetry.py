# Copyright 2026 Google LLC
# apps/agents/base-adk-agent/agent/telemetry.py

import json
import sys
from typing import Any, Dict, Optional
from opentelemetry import trace


class TelemetryEmitter:
    """Type-safe facade to emit structured JSON telemetry events for GCP Cloud Logging jsonPayload ingestion."""

    def _clean_str(self, val: Any) -> str:
        if val is None:
            return "unknown"
        if type(val).__name__ in ("MagicMock", "Mock") or hasattr(val, "_mock_name"):
            return "mock-id"
        return str(val)

    def _get_otel_context(self) -> Dict[str, str]:
        """Extracts active OpenTelemetry trace_id and span_id for Cloud Trace correlation."""
        try:
            span = trace.get_current_span()
            span_ctx = span.get_span_context() if span else None
            if span_ctx and span_ctx.trace_id:
                return {
                    "trace_id": format(span_ctx.trace_id, "032x"),
                    "span_id": format(span_ctx.span_id, "016x"),
                }
        except Exception:
            pass
        return {"trace_id": "unknown_trace", "span_id": "unknown_span"}

    def _emit(self, payload: dict) -> None:
        """Writes raw unformatted JSON directly to stdout for top-level jsonPayload parsing in Cloud Logging."""
        log_line = json.dumps(payload)
        sys.stdout.write(log_line + "\n")
        sys.stdout.flush()

    def emit_token_consumption(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        user_id: str = "anonymous",
        execution_path: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        prompt_tokens: int = 150,
        completion_tokens: int = 85,
        thoughts_tokens: int = 20,
        cached_tokens: int = 0,
        total_tokens: Optional[int] = None,
        finish_reason: str = "STOP",
        turn_index: int = 1,
    ) -> None:
        """Emits a genai_token_consumption event to stdout for Cloud Logging and BigQuery analytics."""
        tot = (
            total_tokens
            if total_tokens is not None
            else (prompt_tokens + completion_tokens + thoughts_tokens)
        )
        cache_hit_ratio = (
            float(cached_tokens) / float(prompt_tokens) if prompt_tokens > 0 else 0.0
        )

        payload = {
            "event": "genai_token_consumption",
            "session_id": self._clean_str(session_id or "sess_remote_default"),
            "user_id": self._clean_str(user_id),
            "agent_id": self._clean_str(agent_id),
            "execution_path": self._clean_str(execution_path or f"{agent_id}@1"),
            "turn_index": turn_index,
            **self._get_otel_context(),
            "model": self._clean_str(model),
            "tokens": {
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "thoughts_tokens": int(thoughts_tokens),
                "cached_tokens": int(cached_tokens),
                "total_tokens": int(tot),
            },
            "implicit_caching": {
                "cache_hit": cached_tokens > 0,
                "cache_hit_ratio": round(cache_hit_ratio, 4),
            },
            "finish_reason": self._clean_str(finish_reason),
        }
        self._emit(payload)

    def emit_mcp_tool(
        self,
        agent_id: str,
        tool_name: str,
        session_id: Optional[str] = None,
        user_id: str = "anonymous",
        status: str = "SUCCESS",
        duration_ms: float = 0.0,
        error_reason: Optional[str] = None,
    ) -> None:
        """Emits an mcp_tool_execution event to stdout for Cloud Logging and BigQuery analytics."""
        payload = {
            "event": "mcp_tool_execution",
            "session_id": self._clean_str(session_id or "unknown_session"),
            "user_id": self._clean_str(user_id),
            "agent_id": self._clean_str(agent_id),
            "tool_name": self._clean_str(tool_name),
            "status": self._clean_str(status),
            "duration_ms": round(duration_ms, 2),
            **self._get_otel_context(),
        }
        if error_reason:
            payload["error_reason"] = self._clean_str(error_reason)
        self._emit(payload)

    def emit_fallback(
        self,
        agent_id: str,
        fallback_type: str,
        error_reason: str,
        session_id: Optional[str] = None,
        invocation_id: Optional[str] = None,
    ) -> None:
        """Emits a fallback_triggered event to stdout for Cloud Logging and BigQuery analytics."""
        payload = {
            "event": "fallback_triggered",
            "agent_id": self._clean_str(agent_id),
            "fallback_type": self._clean_str(fallback_type),
            "error_reason": self._clean_str(error_reason),
            "session_id": self._clean_str(session_id),
            "invocation_id": self._clean_str(invocation_id),
            "status": "FALLBACK_SERVED",
            **self._get_otel_context(),
        }
        self._emit(payload)


telemetry = TelemetryEmitter()
