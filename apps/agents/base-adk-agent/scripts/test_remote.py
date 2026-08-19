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
import json
import httpx
import google.auth
import google.auth.transport.requests
from dotenv import load_dotenv

# Ensure current directory is in PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

def get_gcp_access_token() -> str:
    """Retrieves a Google Cloud OAuth2 access token natively."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        if credentials.token:
            return credentials.token
    except Exception as e:
        print(f"Native credential resolution failed: {e}")
    
    # Fallback to gcloud CLI
    import subprocess
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
    except Exception as ex:
        print(f"gcloud CLI fallback failed: {ex}")
        raise RuntimeError("No valid GCP credentials found.")

async def main(user_input: str):
    PROJECT_ID = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-dev")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID", "1234567890123456789")
    
    base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"
    stream_url = f"{base_url}:streamQuery?alt=sse"

    print("🔑 Fetching GCP credentials...")
    try:
        token = get_gcp_access_token()
    except Exception as e:
        print(f"❌ Auth error: {e}")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    print("📝 1. Provisioning GCP Control Plane Session (Vertex AI Sessions API)...")
    sessions_api_url = f"{base_url}/sessions"
    create_session_payload = {
        "user_id": "test-user-123"
    }

    session_id = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(sessions_api_url, json=create_session_payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            operation_name = data.get("name", "")
            parts = operation_name.split("/")
            if "sessions" in parts:
                session_id = parts[parts.index("sessions") + 1]
            else:
                session_id = parts[-1]
            print(f"✅ Control Plane Session Provisioned (19-Digit Integer ID: {session_id})")
        else:
            print(f"❌ Failed to create session via Control Plane Sessions API: HTTP {resp.status_code} - {resp.text}")
            sys.exit(1)

    if not session_id:
        print("❌ Error: Server did not return a valid session ID.")
        sys.exit(1)

    print("📝 2. Registering 19-Digit Session ID in ADK Agent Runtime (async_create_session)...")
    query_url = f"{base_url}:query"
    adk_create_payload = {
        "class_method": "async_create_session",
        "input": {
            "user_id": "test-user-123",
            "session_id": session_id
        }
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(query_url, json=adk_create_payload, headers=headers)
        if resp.status_code == 200:
            print(f"✅ Session {session_id} successfully registered in ADK Runner SessionStore!")
        else:
            print(f"⚠️ ADK session registration returned HTTP {resp.status_code}: {resp.text}")

    query_payload = {
        "class_method": "async_stream_query",
        "input": {
            "message": user_input,
            "user_id": "test-user-123",
            "session_id": session_id
        }
    }

    print(f"\n📡 Stream Query URL: {stream_url}")
    print(f"💬 Sending query: '{user_input}' (session_id={session_id})")
    print("\n🤖 --- AGENT RESPONSE STREAM ---")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", stream_url, json=query_payload, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    print(f"❌ Error HTTP Status: {response.status_code}")
                    print(f"Details: {response.text}")
                    sys.exit(1)
                
                async for line in response.aiter_lines():
                    if line:
                        data_str = line[5:].strip() if line.startswith("data:") else line.strip()
                        try:
                            data_json = json.loads(data_str)
                            if isinstance(data_json, dict):
                                content = data_json.get("content", {})
                                if isinstance(content, dict) and "parts" in content:
                                    for part in content["parts"]:
                                        if isinstance(part, dict) and "text" in part and part["text"]:
                                            print(part["text"], end="", flush=True)
                                        elif isinstance(part, dict) and "function_call" in part:
                                            print(f"\n[Tool Call: {part['function_call'].get('name')}]", flush=True)
                                elif "output" in data_json:
                                    print(data_json["output"], end="", flush=True)
                                else:
                                    print(json.dumps(data_json), flush=True)
                            else:
                                print(data_json, end="", flush=True)
                        except json.JSONDecodeError:
                            print(data_str, end="", flush=True)
        print()
    except Exception as e:
        print(f"\n❌ Error during execution: {e}")
        import traceback
        traceback.print_exc()

    print("--------------------------------\n")

if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "Can you search documents for Julian Sterling with document_type tax_return?"
    asyncio.run(main(test_query))
