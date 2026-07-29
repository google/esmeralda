import sys
import os
import json
import logging
sys.path.insert(0, 'apps/agents/base-adk-agent')
import asyncio
import google.cloud.logging
from google.cloud.logging.handlers import CloudLoggingHandler
from google.cloud.logging.handlers.transports import SyncTransport

async def test():
    print("Testing direct Cloud Logging emission with SyncTransport...")
    client = google.cloud.logging.Client(project="esmeralda-root-agent-dev")
    handler = CloudLoggingHandler(client, name="reasoning_engine_stdout", transport=SyncTransport)
    custom_logger = logging.getLogger("telemetry_sync")
    custom_logger.addHandler(handler)
    custom_logger.setLevel(logging.INFO)

    payload = {
        "event": "genai_token_consumption",
        "session_id": "test_sync_transport_1000",
        "user_id": "test_user_sync",
        "agent_id": "root_agent",
        "execution_path": "root_agent@1",
        "turn_index": 1,
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "0123456789abcdef",
        "model": "gemini-2.5-flash",
        "tokens": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "thoughts_tokens": 10,
            "cached_tokens": 0,
            "total_tokens": 160
        },
        "implicit_caching": {
            "cache_hit": False,
            "cache_hit_ratio": 0.0
        },
        "finish_reason": "STOP"
    }
    custom_logger.info(json.dumps(payload))
    print("Logged payload via SyncTransport!")

if __name__ == "__main__":
    asyncio.run(test())
