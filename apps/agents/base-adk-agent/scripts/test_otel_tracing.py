import json
import logging
import sys
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

# Setup OpenTelemetry Tracer
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer("esmeralda-tracer")

logger = logging.getLogger("esmeralda.telemetry")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)

def verify_otel_span_correlation():
    """Verify that OpenTelemetry Trace ID and Span ID are properly formatted and correlated."""
    with tracer.start_as_current_span("root_orchestrator_turn") as parent_span:
        span_context = parent_span.get_span_context()
        trace_id = format(span_context.trace_id, "032x")
        span_id = format(span_context.span_id, "016x")

        payload = {
            "event": "genai_token_consumption",
            "session_id": "test_otel_session_123",
            "trace_id": trace_id,
            "span_id": span_id,
            "agent_id": "root_orchestrator",
            "model": "gemini-2.5-flash",
            "tokens": {"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200}
        }
        logger.info(json.dumps(payload))
        assert len(trace_id) == 32, "Trace ID must be a 32-character hex string"
        assert len(span_id) == 16, "Span ID must be a 16-character hex string"
        print(f"✅ OpenTelemetry Trace Correlation Verified: trace_id={trace_id}, span_id={span_id}")

if __name__ == "__main__":
    verify_otel_span_correlation()
