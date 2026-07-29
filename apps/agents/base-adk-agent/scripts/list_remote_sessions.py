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

async def query_engine_sessions(engine_name: str, project_id: str, resource_id: str, location: str, token: str, user_ids: list):
    base_url = f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{location}/reasoningEngines/{resource_id}"
    query_url = f"{base_url}:query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    print(f"\n==================================================")
    print(f"🤖 AGENT: {engine_name}")
    print(f"   Project: {project_id} | Resource ID: {resource_id}")
    print(f"==================================================")

    discovered_sessions = []
    for user_id in user_ids:
        payload = {
            "class_method": "async_list_sessions",
            "input": {
                "user_id": user_id
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
                        print(f"   --> user_id='{user_id}': Found {len(sessions)} session(s)")
                        for sess in sessions:
                            sess["query_user_id"] = user_id
                            discovered_sessions.append(sess)
                else:
                    print(f"   --> user_id='{user_id}': HTTP {resp.status_code}")
            except Exception as e:
                print(f"   --> user_id='{user_id}': Exception {e}")

    print(f"\n   📋 Total Sessions Found for {engine_name}: {len(discovered_sessions)}")
    for idx, sess in enumerate(discovered_sessions, 1):
        sess_id = sess.get("id")
        u_id = sess.get("userId") or sess.get("query_user_id")
        updated = sess.get("lastUpdateTime")
        print(f"   [{idx}] Session ID: {sess_id} | User ID: {u_id} | Updated: {updated}")

        # Fetch session events
        get_payload = {
            "class_method": "async_get_session",
            "input": {
                "user_id": u_id,
                "session_id": sess_id
            }
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                get_resp = await client.post(query_url, json=get_payload, headers=headers)
                if get_resp.status_code == 200:
                    get_data = get_resp.json()
                    sess_details = get_data.get("output", {})
                    events = sess_details.get("events", [])
                    print(f"       Events: {len(events)} | First Role: {events[0].get('content', {}).get('role') if events else 'N/A'} | Last Author: {events[-1].get('author') if events else 'N/A'}")
            except Exception:
                pass

async def main():
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    token = get_gcp_access_token()
    user_ids = ["test-user-123", "default", "admin", "user"]

    # 1. Base ADK Agent (Root Coordinator)
    ROOT_PROJECT_ID = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-dev")
    ROOT_RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID", "1234567890123456789")
    await query_engine_sessions("Base ADK Agent (Root Coordinator)", ROOT_PROJECT_ID, ROOT_RESOURCE_ID, LOCATION, token, user_ids)

    # 2. A2A Agent (Mortgage Tools Specialist)
    A2A_PROJECT_ID = os.getenv("A2A_AGENT_PROJECT_ID", "esmeralda-a2a-agent-dev")
    A2A_RESOURCE_ID = os.getenv("A2A_REASONING_ENGINE_ID", "5748772906326818816")
    await query_engine_sessions("A2A Mortgage Specialist Agent", A2A_PROJECT_ID, A2A_RESOURCE_ID, LOCATION, token, user_ids)

if __name__ == "__main__":
    asyncio.run(main())
