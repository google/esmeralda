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

# Ensure the current directory is in PYTHONPATH so we can import 'agent_app' if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

# Load environment variables
load_dotenv()

# --- CRITICAL: Set environment variables BEFORE Gemini/Vertex SDK instantiation ---
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

import json
import vertexai
import vertexai._genai._agent_engines_utils as utils

# Monkeypatch the reasoning engine client utilities to avoid the SDK schema error when agent_card is empty/None
orig_wrap_v03 = utils._wrap_a2a_operation_v03

def patched_wrap_v03(method_name: str, agent_card: str):
    if not agent_card:
        card_dict = {
            'name': 'a2a-mortgage-agent',
            'description': 'Mortgage underwriting assistant with document management, income verification, and corporate email capabilities.',
            'version': '1.0.0',
            'protocolVersion': '0.3.0',
            'preferredTransport': 'HTTP+JSON',
            'defaultInputModes': ['text/plain'],
            'defaultOutputModes': ['application/json'],
            'url': 'https://us-central1-aiplatform.googleapis.com/v1beta1/projects/agent-ops-foundation-435f/locations/us-central1/reasoningEngines/5403187605623799808/a2a',
            'capabilities': {
                'streaming': False
            },
            'skills': []
        }
        agent_card = json.dumps(card_dict)
    return orig_wrap_v03(method_name, agent_card)

utils._wrap_a2a_operation_v03 = patched_wrap_v03
if hasattr(utils, "_wrap_a2a_operation"):
    utils._wrap_a2a_operation = patched_wrap_v03

from google.genai import types

async def main(user_input: str):
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agent-ops-foundation-435f")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = "5403187605623799808"
    RESOURCE_NAME = f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"

    print("🚀 Initializing vertexai.Client...")
    client = vertexai.Client(
        project=PROJECT_ID,
        location=LOCATION,
        http_options=types.HttpOptions(
            api_version="v1beta1",
        )
    )
    print("✅ Client initialized.")

    print(f"\n📡 Getting remote agent engine resource: {RESOURCE_NAME}")
    remote_agent = client.agent_engines.get(name=RESOURCE_NAME)
    print("✅ Remote agent retrieved.")
    print(remote_agent)

    # 1. Retrieve the Agent Card
    print("\n📇 --- 1. RETRIEVING AUTHENTICATED AGENT CARD ---")
    try:
        card = await remote_agent.handle_authenticated_agent_card()
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
    message_data = {
        "messageId": f"remote-test-{uuid.uuid4()}",
        "role": "user",
        "parts": [{"kind": "text", "text": user_input}],
    }
    print(f"Sending message payload:\n{message_data}\n")
    
    try:
        response = await remote_agent.on_message_send(**message_data)
        print("\n🤖 Agent Response (on_message_send):")
        print(response)
        sys.stdout.flush()

        # Helper function to recursively find a task ID inside any returned structure
        def extract_task_id(obj):
            if not obj:
                return None
            if isinstance(obj, dict):
                return obj.get("taskId") or obj.get("id")
            if isinstance(obj, str):
                return None
            if hasattr(obj, "taskId") and getattr(obj, "taskId"):
                val = getattr(obj, "taskId")
                if isinstance(val, str):
                    return val
            if hasattr(obj, "id") and getattr(obj, "id"):
                val = getattr(obj, "id")
                if isinstance(val, str):
                    return val
            if isinstance(obj, (list, tuple)):
                for item in obj:
                    tid = extract_task_id(item)
                    if tid:
                        return tid
            return None

        task_id = extract_task_id(response)

        if task_id:
            print(f"\n📋 --- 3. CHECKING TASK STATUS FOR TASK ID: {task_id} (on_get_task) ---")
            task_data = {
                "id": task_id,
            }
            task_response = await remote_agent.on_get_task(**task_data)
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
