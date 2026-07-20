import json
import logging
import sys

logger = logging.getLogger("esmeralda.telemetry")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)

def simulate_runaway_loop_breach():
    """Simulate single request breaching 50,000 token limit."""
    print("🔥 [CHAOS TEST] Emitting 55,000 token single-request payload...")
    payload = {
        "event": "genai_token_consumption",
        "agent_id": "root_orchestrator",
        "execution_path": "root_agent@1/mortgage_tools_agent@1",
        "session_id": "chaos_test_session_999",
        "user_id": "chaos_tester@google.com",
        "model": "gemini-2.5-flash",
        "tokens": {
            "prompt_tokens": 45000,
            "completion_tokens": 5000,
            "thoughts_tokens": 5000,
            "cached_tokens": 10000,
            "total_tokens": 55000
        }
    }
    logger.info(json.dumps(payload))
    print("✅ High-token event emitted to stdout. Verify alert 'Runaway Agent Loop' in 60s.")

def simulate_mcp_error():
    """Simulate Cloud Run MCP tool failure."""
    print("⚡ [CHAOS TEST] Emitting MCP Tool Execution Error...")
    payload = {
        "event": "mcp_tool_execution",
        "tool_name": "verify_income",
        "mcp_service": "income-verification",
        "status": "ERROR",
        "error_code": 500,
        "error_message": "Downstream legacy system timeout"
    }
    logger.info(json.dumps(payload))
    print("✅ MCP Error event emitted.")

if __name__ == "__main__":
    print("🚀 Starting Esmeralda Governance Pipeline Chaos Test...")
    simulate_runaway_loop_breach()
    simulate_mcp_error()
    print("🎉 Chaos simulation complete.")
