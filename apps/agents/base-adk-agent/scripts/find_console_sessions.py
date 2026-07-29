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
    
    import subprocess
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
    except Exception as ex:
        print(f"gcloud CLI fallback failed: {ex}")
        raise RuntimeError("No valid GCP credentials found.")

async def main():
    PROJECT_ID = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-3a3d")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID", "864970954164404224")
    
    base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"
    query_url = f"{base_url}:query"

    print("🔑 Fetching GCP credentials...")
    token = get_gcp_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Candidate user_ids used by GCP Console Playground / Vertex AI Studio
    candidate_user_ids = [
        "afonsomenegola",
        "afonsomenegola@google.com",
        "console",
        "playground",
        "default",
        "user",
        "admin",
        "system",
        "test-user-123",
        "google-user",
        "vertex-console",
        "vertex-playground"
    ]

    print("\n🔍 --- 1. SEARCHING SESSIONS ACROSS CANDIDATE USER_IDs ---")
    found_any = False
    for uid in candidate_user_ids:
        payload = {
            "class_method": "async_list_sessions",
            "input": {
                "user_id": uid
            }
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(query_url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    output = data.get("output", {})
                    sessions = output.get("sessions", []) if isinstance(output, dict) else []
                    if sessions:
                        found_any = True
                        print(f"✅ FOUND {len(sessions)} SESSION(S) for user_id='{uid}':")
                        for s in sessions:
                            print(f"   • Session ID: {s.get('id')} | User ID: {s.get('userId')} | Last Updated: {s.get('lastUpdateTime')}")
            except Exception as e:
                print(f"   Failed for user_id='{uid}': {e}")

    if not found_any:
        print("   No sessions found for candidate user_ids.")

    print("\n🔍 --- 2. QUERYING VERTEX AI ENGINE API DIRECTLY ---")
    # Query direct Vertex AI Reasoning Engine REST endpoint for sessions resource if supported
    sessions_direct_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}/sessions"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(sessions_direct_url, headers=headers)
            print(f"GET /sessions HTTP Status: {resp.status_code}")
            if resp.status_code == 200:
                print(json.dumps(resp.json(), indent=2))
            else:
                print(f"Response: {resp.text[:200]}")
        except Exception as e:
            print(f"GET /sessions failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
