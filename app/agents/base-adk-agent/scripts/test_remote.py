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
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "esmeralda-root-agent-918f")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = "35393829053923328"
    
    # Reasoning Engine REST API stream URL
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

    query_payload = {
        "class_method": "async_stream_query",
        "input": {
            "message": user_input,
            "user_id": "test-user-123"
        }
    }


    print(f"\n📡 Stream Query URL: {stream_url}")
    print(f"💬 Sending query: '{user_input}'")
    print("\n🤖 --- AGENT RESPONSE STREAM ---")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", stream_url, json=query_payload, headers=headers) as response:
                if response.status_code != 200:
                    # Read response body for error details
                    await response.aread()

                    print(f"❌ Error HTTP Status: {response.status_code}")
                    print(f"Details: {response.text}")
                    sys.exit(1)
                
                async for line in response.aiter_lines():
                    if line:
                        # Parse SSE format
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                data_json = json.loads(data_str)
                                # Extract content from response structure if present
                                if isinstance(data_json, dict) and "output" in data_json:
                                    print(data_json["output"], end="", flush=True)
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
    test_query = sys.argv[1] if len(sys.argv) > 1 else "Can you verify Julian Sterling's income?"
    asyncio.run(main(test_query))
