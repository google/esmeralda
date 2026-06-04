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

# Ensure the current directory is in PYTHONPATH so we can import 'app' and 'agent'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

# Load environment variables (from .env or set them manually for testing)
load_dotenv()

# Explicitly set required env vars for testing if missing
# Replace these with your actual test values or ensure they are in your .env
if not os.getenv("GOOGLE_CLOUD_PROJECT"):
    os.environ["GOOGLE_CLOUD_PROJECT"] = "agent-ops-foundation-8d47"
if not os.getenv("EVENTS_DATASET_ID"):
    os.environ["EVENTS_DATASET_ID"] = "agent_logs"
if not os.getenv("EVENTS_TABLE_ID"):
    os.environ["EVENTS_TABLE_ID"] = "agent_events"
if not os.getenv("DMS_MCP_URL"):
    os.environ["DMS_MCP_URL"] = "https://dms.internal.gateway/mcp"
if not os.getenv("INCOME_VERIFICATION_URL"):
    os.environ["INCOME_VERIFICATION_URL"] = "https://income-verification.internal.gateway/mcp"
if not os.getenv("EMAIL_MCP_URL"):
    os.environ["EMAIL_MCP_URL"] = "https://email.internal.gateway/mcp"
if not os.getenv("GCS_BUCKET"):
     os.environ["GCS_BUCKET"] = "agent-ops-foundation-agent-logs-offload-8d47"

# --- Configuration ---
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project-id")
DATASET_ID = os.environ.get("BIG_QUERY_DATASET_ID", "your-big-query-dataset-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "US")
GCS_BUCKET = os.environ.get("GCS_BUCKET_NAME", "your-gcs-bucket-name")

if PROJECT_ID == "your-gcp-project-id":
    raise ValueError("Please set GOOGLE_CLOUD_PROJECT or update the code.")

# --- CRITICAL: Set environment variables BEFORE Gemini instantiation ---
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

try:
    from agent_app import adk_app
    from google.adk.runners import InMemoryRunner
except ImportError as e:
    print(f"Error importing app: {e}")
    print("Ensure you are running this script from the 'agents/a2a-agent' directory or have PYTHONPATH set correctly.")
    sys.exit(1)

async def main():
    print("🚀 Initializing Local Runner...")

    runner = InMemoryRunner(app=adk_app)

    print("✅ Runner initialized. Sending test query...")

    user_input = "I'm reviewing the Rivera family's $700K loan. Can you summarize their 2024 tax returns?"
    print(f"User: {user_input}")

    try:
        if hasattr(runner, "run_debug"):
             response = await runner.run_debug(user_input)
        else:
             response = await runner.run(user_input)

        print("\n🤖 Agent Response:")
        print(response)

    except Exception as e:
        print(f"\n❌ An error occurred during execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
