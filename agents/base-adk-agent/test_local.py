# Copyright 2025 Google LLC
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

import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure the current directory is in PYTHONPATH so we can import 'app' and 'root_agent'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

# Load environment variables (from .env or set them manually for testing)
load_dotenv()

# Explicitly set required env vars for testing if missing
# First fallback to PROJECT_ID from root .env if GOOGLE_CLOUD_PROJECT is not set
if not os.getenv("GOOGLE_CLOUD_PROJECT"):
    os.environ["GOOGLE_CLOUD_PROJECT"] = os.getenv("PROJECT_ID", "agent-ops-foundation-953d")
if not os.getenv("EVENTS_DATASET_ID"):
    os.environ["EVENTS_DATASET_ID"] = "agent_logs"
if not os.getenv("EVENTS_TABLE_ID"):
    os.environ["EVENTS_TABLE_ID"] = "agent_events"
if not os.getenv("A2A_AGENT_URL"):
    os.environ["A2A_AGENT_URL"] = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/693826639943/locations/us-central1/reasoningEngines/7033983251941163008/a2a"
if not os.getenv("GCS_BUCKET"):
     os.environ["GCS_BUCKET"] = os.getenv("GCS_OFFLOAD_BUCKET_NAME", "agent-ops-foundation-agent-logs-offload-953d")

# --- Configuration ---
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
DATASET_ID = os.environ.get("BIG_QUERY_DATASET_ID", "agent_logs")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "agent-ops-foundation-agent-logs-offload-953d")

if not PROJECT_ID or PROJECT_ID == "your-gcp-project-id":
    raise ValueError("Please set GOOGLE_CLOUD_PROJECT or update the code.")

# --- CRITICAL: Set environment variables BEFORE Gemini instantiation ---
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

try:
    from agent_app import adk_app
except ImportError as e:
    print(f"Error importing adk_app: {e}")
    print("Ensure you are running this script from the 'agents/base-adk-agent' directory or have PYTHONPATH set correctly.")
    sys.exit(1)

async def main(user_input: str):
    print("🚀 Initializing AdkApp configuration...")
    
    # Run the setup which configures Cloud Logging and OTel BaggageSpanProcessor
    adk_app.set_up()
    
    print("✅ AdkApp initialized. Sending test query via async_stream_query...")
    print(f"User: {user_input}\n")

    caller_context = {
        "project_id": "team-a-billing-project",
        "agent_name": "esmeralda-caller-agent"
    }

    print("--- INITIATING ASYNC STREAM QUERY ---")
    try:
        # Call async_stream_query directly with adk_app
        async for event in adk_app.async_stream_query(
            message=user_input,
            user_id="local-test-user",
            caller_context=caller_context
        ):
            if isinstance(event, dict):
                content = event.get("content", str(event))
            elif hasattr(event, "content"):
                content = event.content
            else:
                content = str(event)
            print(content, end="", flush=True)
        
        print("\n\n--- STREAM COMPLETED ---")

    except Exception as e:
        print(f"\n❌ An error occurred during execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "I'm reviewing the Rivera family's $700K loan. Can you summarize their 2024 tax returns?"
    asyncio.run(main(test_query))
