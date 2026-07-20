import json
import logging
import sys
from typing import Any
from google.adk.plugins import BasePlugin
from opentelemetry import trace

# Setup JSON telemetry logger writing to stdout
logger = logging.getLogger("esmeralda.telemetry")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)

class EsmeraldaTelemetryPlugin(BasePlugin):
    """
    Enterprise ADK Telemetry & Governance Plugin for Esmeralda Multi-Agent Platform.
    Hooks into LLM model responses and tool executions to emit structured JSON logs to stdout.
    """

    def __init__(self, agent_name: str = "root_orchestrator"):
        super().__init__()
        self.agent_name = agent_name

    def on_model_finish(
        self,
        model_context: Any,
        model_response: Any,
        **kwargs: Any
    ) -> None:
        """Hook triggered after Gemini model generation completes."""
        usage = getattr(model_response, "usage_metadata", None)
        if not usage:
            return

        # Extract OTel Trace ID & Span ID for Cloud Trace correlation
        current_span = trace.get_current_span()
        span_context = current_span.get_span_context() if current_span else None
        trace_id = format(span_context.trace_id, "032x") if span_context and span_context.trace_id else "unknown_trace"
        span_id = format(span_context.span_id, "016x") if span_context and span_context.span_id else "unknown_span"

        # Extract Token & Caching Metrics
        cached_count = getattr(usage, "cached_content_token_count", 0) or getattr(usage, "cached_token_count", 0) or 0
        prompt_count = getattr(usage, "prompt_token_count", 0)
        cache_hit_ratio = float(cached_count) / float(prompt_count) if prompt_count > 0 else 0.0

        # Construct Rich Application-Level Telemetry Event
        payload = {
            "event": "genai_token_consumption",
            # 1. Identity & Session Context
            "session_id": getattr(model_context, "session_id", "unknown_session"),
            "user_id": getattr(model_context, "user_id", getattr(model_context, "state", {}).get("user_id", "anonymous")),
            "agent_id": self.agent_name,
            "execution_path": getattr(model_context, "execution_path", f"{self.agent_name}@1"),
            "turn_index": getattr(model_context, "turn_index", getattr(model_context, "state", {}).get("turn_index", 1)),

            # 2. Distributed Tracing & Correlation
            "trace_id": trace_id,
            "span_id": span_id,

            # 3. Model & Token Accounting (Gemini 2.5 Nuances)
            "model": getattr(model_context, "model_name", "gemini-2.5-flash"),
            "tokens": {
                "prompt_tokens": prompt_count,
                "completion_tokens": getattr(usage, "candidates_token_count", 0),
                "thoughts_tokens": getattr(usage, "thoughts_token_count", 0), # CoT Reasoning Tokens
                "cached_tokens": cached_count,
                "total_tokens": getattr(usage, "total_token_count", 0),
            },
            "implicit_caching": {
                "cache_hit": cached_count > 0,
                "cache_hit_ratio": round(cache_hit_ratio, 4),
            },

            # 4. Safety & Finish Reason
            "finish_reason": getattr(model_response, "finish_reason", "STOP"),
        }
        # Emit single-line JSON payload to stdout (parsed by Cloud Logging in 1-5s)
        logger.info(json.dumps(payload))

    def on_tool_finish(
        self,
        tool_context: Any,
        tool_response: Any,
        tool_name: str,
        duration_ms: float,
        status: str = "SUCCESS",
        **kwargs: Any
    ) -> None:
        """Hook triggered after an MCP or local tool execution completes."""
        payload = {
            "event": "mcp_tool_execution",
            "session_id": getattr(tool_context, "session_id", "unknown_session"),
            "user_id": getattr(tool_context, "user_id", "anonymous"),
            "agent_id": self.agent_name,
            "tool_name": tool_name,
            "mcp_service": getattr(tool_context, "mcp_service", "unknown_service"),
            "tool_type": getattr(tool_context, "tool_type", "MCP"),
            "duration_ms": round(duration_ms, 2),
            "status": status,
        }
        logger.info(json.dumps(payload))
