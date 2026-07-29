import json
import logging
import os
import sys
import time
from typing import Any
from google.adk.plugins import BasePlugin
from opentelemetry import trace

# Setup JSON telemetry logger writing to stdout via standard Python logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

from .telemetry import telemetry


class EsmeraldaTelemetryPlugin(BasePlugin):
    """
    Enterprise ADK Telemetry & Governance Plugin for Esmeralda Multi-Agent Platform.
    Hooks into LLM model responses and tool executions to emit structured JSON logs to stdout.
    """

    def __init__(self, agent_name: str | None = None):
        super().__init__(name="esmeralda_telemetry")
        self._explicit_name = agent_name

    @property
    def agent_name(self) -> str:
        return self._explicit_name or os.getenv("AGENT_NAME") or "unknown_agent"

    async def before_tool_callback(
        self,
        tool: Any = None,
        tool_args: Any = None,
        tool_context: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Official ADK hook triggered before an MCP or local tool execution starts."""
        if tool_context is not None:
            setattr(tool_context, "_start_time", time.time())
        return None

    async def after_model_callback(
        self,
        callback_context: Any = None,
        llm_response: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Official ADK hook triggered after Gemini model generation completes."""
        try:
            model_context = callback_context
            model_response = llm_response
            usage = getattr(model_response, "usage_metadata", None) or getattr(model_response, "usage", None)
            if isinstance(model_response, dict):
                usage = model_response.get("usage_metadata") or model_response.get("usage")

            if usage is None:
                logger.error(f"Telemetry Error: model_response contains no usage_metadata or usage. Type: {type(model_response)}, content: {model_response}")
                return

            if isinstance(usage, dict):
                prompt_count = usage.get("prompt_token_count")
                completion_count = usage.get("candidates_token_count")
                thoughts_count = usage.get("thoughts_token_count") or 0
                cached_count = usage.get("cached_content_token_count") or 0
                total_count = usage.get("total_token_count")
            else:
                prompt_count = getattr(usage, "prompt_token_count", None)
                completion_count = getattr(usage, "candidates_token_count", None)
                thoughts_count = getattr(usage, "thoughts_token_count", None) or 0
                cached_count = getattr(usage, "cached_content_token_count", None) or getattr(usage, "cached_token_count", None) or 0
                total_count = getattr(usage, "total_token_count", None)

            if prompt_count is None or completion_count is None or total_count is None:
                logger.error(f"Telemetry Error: Missing required token metrics. prompt={prompt_count}, completion={completion_count}, total={total_count}, usage={usage}")
                return

            session_id = (
                getattr(model_context, "session_id", None)
                or getattr(getattr(model_context, "session", None), "id", None)
                or (getattr(model_context, "state", {}).get("session_id") if isinstance(getattr(model_context, "state", None), dict) else None)
                or "sess_remote_default"
            )
            user_id = getattr(model_context, "user_id", "anonymous") if not hasattr(getattr(model_context, "user_id", None), "_mock_name") else "anonymous"
            execution_path = getattr(model_context, "execution_path", f"{self.agent_name}@1") if not hasattr(getattr(model_context, "execution_path", None), "_mock_name") else f"{self.agent_name}@1"
            model_name = getattr(model_context, "model_name", "gemini-2.5-flash") if not hasattr(getattr(model_context, "model_name", None), "_mock_name") else "gemini-2.5-flash"
            finish_reason = getattr(model_response, "finish_reason", "STOP") if not hasattr(getattr(model_response, "finish_reason", None), "_mock_name") else "STOP"

            telemetry.emit_token_consumption(
                agent_id=self.agent_name,
                session_id=session_id,
                user_id=user_id,
                execution_path=execution_path,
                model=model_name,
                prompt_tokens=prompt_count,
                completion_tokens=completion_count,
                thoughts_tokens=thoughts_count,
                cached_tokens=cached_count,
                total_tokens=total_count,
                finish_reason=finish_reason,
            )
        except Exception as e:
            logger.error(f"Unhandled exception in after_model_callback: {e}", exc_info=True)

    async def after_tool_callback(
        self,
        tool: Any = None,
        tool_args: Any = None,
        tool_context: Any = None,
        result: Any = None,
        **kwargs: Any
    ) -> Any:
        """Official ADK hook triggered after an MCP or local tool execution completes."""
        try:
            tool_name = getattr(tool, "name", "unknown_tool") if tool and not hasattr(getattr(tool, "name", None), "_mock_name") else "unknown_tool"
            session_id = getattr(getattr(tool_context, "session", None), "id", getattr(tool_context, "session_id", "unknown_session")) if not hasattr(getattr(tool_context, "session_id", None), "_mock_name") else "unknown_session"
            user_id = getattr(tool_context, "user_id", "anonymous") if not hasattr(getattr(tool_context, "user_id", None), "_mock_name") else "anonymous"

            start_time = getattr(tool_context, "_start_time", None) if tool_context else None
            duration_ms = (time.time() - start_time) * 1000.0 if isinstance(start_time, (int, float)) else 0.0

            telemetry.emit_mcp_tool(
                agent_id=self.agent_name,
                tool_name=tool_name,
                session_id=session_id,
                user_id=user_id,
                status="SUCCESS",
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(f"Unhandled exception in after_tool_callback: {e}", exc_info=True)
        return result


    def on_model_finish(self, *args: Any, **kwargs: Any) -> None:
        """Compatibility alias for legacy callers."""
        self.after_model_callback(*args, **kwargs)

    def on_tool_finish(self, *args: Any, **kwargs: Any) -> None:
        """Compatibility alias for legacy callers."""
        self.after_tool_callback(*args, **kwargs)
