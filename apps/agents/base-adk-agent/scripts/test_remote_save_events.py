# Copyright 2026 Google LLC
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

OUTPUT_FILE_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../../../.gemini/jetski/brain/34f2b414-e2ec-4c72-9e7f-dcc910b36109/scratch/remote_events_sequence.json"
    )
)

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
    PROJECT_ID = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-3a3d")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID", "864970954164404224")
    
    base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"
    stream_url = f"{base_url}:streamQuery?alt=sse"

    print("🔑 Fetching GCP credentials...")
    token = get_gcp_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    print("📝 Creating persistent session on Vertex AI Agent Engine...")
    query_url = f"{base_url}:query"
    create_session_payload = {
        "class_method": "async_create_session",
        "input": {
            "user_id": "test-user-123"
        }
    }

    session_id = None
    session_response_raw = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(query_url, json=create_session_payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                session_response_raw = data
                output = data.get("output", {})
                if isinstance(output, dict):
                    session_id = output.get("id") or output.get("name", "").split("/")[-1]
                print(f"✅ Session Created: {session_id}")
            else:
                print(f"⚠️ Create Session returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"⚠️ Create Session call failed: {e}")

    query_payload = {
        "class_method": "async_stream_query",
        "input": {
            "message": user_input,
            "user_id": "test-user-123",
        }
    }
    if session_id:
        query_payload["input"]["session_id"] = session_id

    print(f"\n📡 Stream Query URL: {stream_url}")
    print(f"💬 Sending query: '{user_input}' (session_id={session_id})")
    print("\n🤖 --- AGENT RESPONSE STREAM ---")
    
    events_sequence = []
    events_sequence.append({
        "event_type": "create_session_response",
        "payload": session_response_raw
    })

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", stream_url, json=query_payload, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    print(f"❌ Error HTTP Status: {response.status_code}")
                    print(f"Details: {response.text}")
                    sys.exit(1)
                
                event_counter = 0
                async for line in response.aiter_lines():
                    if line:
                        data_str = line[5:].strip() if line.startswith("data:") else line.strip()
                        try:
                            data_json = json.loads(data_str)
                            event_counter += 1
                            events_sequence.append({
                                "event_index": event_counter,
                                "raw_line": line,
                                "parsed_json": data_json
                            })
                            
                            # Print text preview
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
                                    print(f"[Event #{event_counter}]", flush=True)
                            else:
                                print(data_json, end="", flush=True)
                        except json.JSONDecodeError:
                            print(data_str, end="", flush=True)
        print()
    except Exception as e:
        print(f"\n❌ Error during execution: {e}")
        import traceback
        traceback.print_exc()

    get_session_data = None
    if session_id:
        print("\n🔍 Fetching persisted session from Vertex AI Agent Engine...")
        get_session_payload = {
            "class_method": "async_get_session",
            "input": {
                "user_id": "test-user-123",
                "session_id": session_id
            }
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(query_url, json=get_session_payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    get_session_data = data
                    print(f"✅ Session Retrieved Successfully!")
                else:
                    print(f"⚠️ Get Session returned status {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"⚠️ Get Session call failed: {e}")

    events_sequence.append({
        "event_type": "get_session_response",
        "payload": get_session_data
    })

    # Save sequence to output file
    os.makedirs(os.path.dirname(OUTPUT_FILE_PATH), exist_ok=True)
    with open(OUTPUT_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(events_sequence, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Saved complete event sequence ({len(events_sequence)} items) to:")
    print(f"   {OUTPUT_FILE_PATH}")
    print("--------------------------------\n")

if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "Can you verify Julian Sterling's income?"
    asyncio.run(main(test_query))
