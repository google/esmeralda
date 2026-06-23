#!/usr/bin/env python3
"""
Test script to verify Cross-Agent FinOps context propagation locally on Esmeralda base-adk-agent.
This simulates "Team A" calling "Team B's Agent Engine" with a `caller_context` parameter.
"""

import asyncio
import os
import sys
import logging
from dotenv import load_dotenv

# Ensure the current directory is in PYTHONPATH so we can import 'agent_app'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

# Load environment variables (from .env or set them manually for testing)
load_dotenv()

# Explicitly set required env vars for testing if missing
if not os.getenv("GOOGLE_CLOUD_PROJECT"):
    os.environ["GOOGLE_CLOUD_PROJECT"] = "gcp-dev-share"
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# Configure logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_observability_v2")

# Import the actual deployed ADK App configurations
try:
    from agent_app import adk_app
except ImportError as e:
    logger.error(f"Failed to import adk_app from agent_app: {e}")
    sys.exit(1)

async def run_finops_test(query: str):
    logger.info("Initializing real AdkApp configuration from agent_app.py...")
    
    # Run the setup which configures Cloud Logging and OTel BaggageSpanProcessor
    adk_app.set_up()
    
    # Construct the simulated caller/billing context representing Team A
    caller_context = {
        "project_id": "team-a-billing-project",
        "agent_name": "esmeralda-caller-agent"
    }

    logger.info(f"Sending query: '{query}' with caller_context: {caller_context}")
    print("\n--- INITIATING QUERY ---")

    try:
        # Pass `caller_context` directly as a keyword argument!
        # The custom AdkApp interceptor inside agent_app.py will:
        #   1. Intercept 'caller_context'
        #   2. Inject it into standard OpenTelemetry Baggage
        #   3. Execute the agent query normally (excluding caller_context so the agent doesn't crash)
        #   4. Detach context and trigger a force_flush on exit
        response = adk_app.query(
            input=query,
            caller_context=caller_context
        )
        print("\n🤖 Agent Response:")
        print(response)
        sys.stdout.flush()
        
    except Exception as e:
        logger.error(f"Error during execution: {e}", exc_info=True)
        raise e
    finally:
        print("-----------------------\n")
        logger.info("Execution complete. The AdkApp's interceptor has automatically executed force_flush.")

if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "Hello, who are you and what can you do?"
    asyncio.run(run_finops_test(test_query))
