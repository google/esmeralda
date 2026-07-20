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

async def main(session_id: str):
    PROJECT_ID = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-3a3d")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID", "864970954164404224")
    
    base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"
    query_url = f"{base_url}:query"

    token = get_gcp_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    print(f"📖 Fetching session history for session_id='{session_id}'...")
    get_payload = {
        "class_method": "async_get_session",
        "input": {
            "user_id": "test-user-123",
            "session_id": session_id
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(query_url, json=get_payload, headers=headers)
        if resp.status_code != 200:
            print(f"❌ HTTP {resp.status_code}: {resp.text}")
            sys.exit(1)
        
        data = resp.json()
        output = data.get("output", {})
        events = output.get("events", [])

        print(f"\n==================================================")
        print(f"💬 CONVERSATION TRANSCRIPT FOR SESSION: {session_id}")
        print(f"==================================================\n")

        for idx, event in enumerate(events, 1):
            author = event.get("author", "N/A")
            timestamp = event.get("timestamp")
            content = event.get("content", {})
            role = content.get("role", "N/A") if isinstance(content, dict) else "N/A"
            parts = content.get("parts", []) if isinstance(content, dict) else []

            print(f"--- [Turn {idx}] Author: {author} | Role: {role} | Timestamp: {timestamp} ---")
            
            for part in parts:
                if isinstance(part, dict):
                    if part.get("text"):
                        print(f"💬 {part['text']}")
                    
                    fc = part.get("functionCall") or part.get("function_call")
                    if isinstance(fc, dict):
                        print(f"🛠️ Tool Call: {fc.get('name')}({json.dumps(fc.get('args', {}))})")
                    
                    fr = part.get("functionResponse") or part.get("function_response")
                    if isinstance(fr, dict):
                        print(f"📥 Tool Output [{fr.get('name')}]: {json.dumps(fr.get('response', {}))}")
            print("-" * 50)

if __name__ == "__main__":
    target_session_id = sys.argv[1] if len(sys.argv) > 1 else "895733846400565248"
    asyncio.run(main(target_session_id))
