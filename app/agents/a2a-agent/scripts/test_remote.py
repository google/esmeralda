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
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load environment variables
load_dotenv()

# --- CRITICAL: Set environment variables BEFORE Gemini/Vertex SDK instantiation ---
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

import google.auth
import google.auth.transport.requests
import json
import vertexai
from google.genai import types

async def main(user_input: str):
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "esmeralda-a2a-918f")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = os.getenv("REASONING_ENGINE_ID", "5748772906326818816")
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

    override_url = os.getenv("AGENT_URL")
    if override_url and hasattr(remote_agent, "agent_card"):
        print(f"🔗 Overriding Agent Card URL for external test runner: {override_url}")
        remote_agent.agent_card.url = override_url

    # 1. Retrieve the Agent Card via SDK
    print("\n📇 --- 1. RETRIEVING AUTHENTICATED AGENT CARD ---")
    try:
        card = await remote_agent.handle_authenticated_agent_card()
        print("\n💼 Deployed A2A Agent Card (via SDK):")
        print(card)
    except Exception as e:
        print(f"❌ Card fetch exception: {e}")
    print("-------------------------------------------------------------------------\n")

    # 2. Send Message via A2A ClientFactory (Direct to Public Endpoint)
    print("💬 --- 2. SENDING MESSAGE VIA A2A CLIENTFACTORY ---")
    import httpx
    from a2a.client import ClientFactory, ClientConfig
    from a2a.types import TransportProtocol, Message, TextPart

    # Get GCP Auth Bearer Token for Vertex AI Reasoning Engine REST API
    credentials, _ = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    token = credentials.token

    public_url = f"https://us-central1-aiplatform.googleapis.com/v1beta1/{RESOURCE_NAME}/a2a"
    print(f"📡 Connecting to Public REST Endpoint: {public_url}")
    
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(timeout=60.0, headers={"Authorization": f"Bearer {token}"}),
        supported_transports=[TransportProtocol.http_json],
    )
    factory = ClientFactory(config)
    
    from a2a.types import AgentCard
    local_card_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "a2a", "agent.json")
    with open(local_card_path, "r") as f:
        card_data = f.read()
    
    card_for_test = AgentCard.model_validate_json(card_data)
    card_for_test.url = public_url
    card_for_test.additional_interfaces = None
    client = factory.create(card_for_test)

    message = Message(
        message_id=f"remote-test-{uuid.uuid4()}",
        role="user",
        parts=[TextPart(text=user_input)],
    )

    try:
        response_stream = client.send_message(message)
        print("\n🤖 Agent Response (Stream):")
        async for chunk in response_stream:
            print(chunk)
        print("\n✅ Message exchange SUCCESSFUL!")
    except Exception as e:
        print(f"\n❌ Message exchange error: {e}")
        import traceback
        traceback.print_exc()
    print("-------------------------------------------------------------------------\n")

if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "I'm reviewing the Rivera family's $700K loan. Can you summarize their 2024 tax returns?"
    asyncio.run(main(test_query))
