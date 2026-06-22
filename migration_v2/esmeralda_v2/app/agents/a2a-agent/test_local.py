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
import uuid
from dotenv import load_dotenv

# Ensure the current directory is in PYTHONPATH so we can import 'agent_app'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

# Load environment variables (from .env or set them manually for testing)
load_dotenv()

# Explicitly set required env vars for testing if missing
if not os.getenv("GOOGLE_CLOUD_PROJECT"):
    os.environ["GOOGLE_CLOUD_PROJECT"] = os.getenv("PROJECT_ID", "agent-ops-foundation-435f")
if not os.getenv("EVENTS_DATASET_ID"):
    os.environ["EVENTS_DATASET_ID"] = "agent_logs"
if not os.getenv("EVENTS_TABLE_ID"):
    os.environ["EVENTS_TABLE_ID"] = "agent_events"
if not os.getenv("GCS_BUCKET"):
     os.environ["GCS_BUCKET"] = os.getenv("GCS_OFFLOAD_BUCKET_NAME", "agent-ops-foundation-agent-logs-offload-435f")

# Local fallback URLs for MCP Servers
if not os.getenv("DMS_MCP_URL"):
    os.environ["DMS_MCP_URL"] = "http://localhost:8000/mcp"
if not os.getenv("INCOME_VERIFICATION_URL"):
    os.environ["INCOME_VERIFICATION_URL"] = "http://localhost:8001/mcp"
if not os.getenv("EMAIL_MCP_URL"):
    os.environ["EMAIL_MCP_URL"] = "http://localhost:8002/mcp"

# --- CRITICAL: Set environment variables BEFORE Gemini instantiation ---
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

try:
    from agent_app import adk_app
except ImportError as e:
    print(f"Error importing adk_app: {e}")
    print("Ensure you are running this script from the 'agents/remotes/a2a-agent' directory or have PYTHONPATH set correctly.")
    sys.exit(1)

async def run_maybe_async(func, *args, **kwargs):
    """Safely runs a method whether it is implemented as a sync or async function."""
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    return func(*args, **kwargs)

async def main(user_input: str):
    import a2a.types

    print("🚀 Initializing local A2A agent app...")
    adk_app.set_up()
    print("✅ App initialized.\n")
    print(f"User Input: {user_input}\n")

    # 1. Retrieve the Agent Card
    print("📇 --- 1. RETRIEVING AUTHENTICATED AGENT CARD ---")
    try:
        card = adk_app.agent_card
        print("\n💼 Deployed A2A Agent Card:")
        print(card)
        sys.stdout.flush()
    except Exception as e:
        print(f"\n❌ Failed to retrieve agent card: {e}")
        import traceback
        traceback.print_exc()
    print("-------------------------------------------------------------------------\n")

    # 2. Send Message (on_message_send)
    print("💬 --- 2. SENDING MESSAGE (on_message_send) ---")
    try:
        part = a2a.types.TextPart(kind='text', text=user_input)
        msg = a2a.types.Message(
            message_id=f"local-test-{uuid.uuid4()}",
            role=a2a.types.Role.user,
            parts=[part],
            kind='message'
        )
        params = a2a.types.MessageSendParams(message=msg)
        print(f"Sending message payload:\n{params}\n")

        response = await adk_app.request_handler.on_message_send(params)
        print("\n🤖 Agent Response (on_message_send):")
        print(response)
        sys.stdout.flush()

        # 3. Get Task (on_get_task) if a Task ID was returned
        task_id = None
        if hasattr(response, "id"):
            task_id = response.id
        elif isinstance(response, dict):
            task_id = response.get("id")

        if task_id:
            print(f"\n📋 --- 3. CHECKING TASK STATUS FOR TASK ID: {task_id} (on_get_task) ---")
            query_params = a2a.types.TaskQueryParams(id=task_id)
            task_response = await adk_app.request_handler.on_get_task(query_params)
            print("\n📋 Task status response:")
            print(task_response)
        else:
            print("\n📋 No taskId returned in on_message_send response; skipping on_get_task check.")

    except Exception as e:
        print(f"\n❌ An error occurred during A2A message exchange: {e}")
        import traceback
        traceback.print_exc()
    print("-------------------------------------------------------------------------\n")

if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "I'm reviewing the Rivera family's $700K loan. Can you summarize their 2024 tax returns?"
    asyncio.run(main(test_query))
