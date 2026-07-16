import os
import sys
import json
import httpx
import google.auth
from google.auth.transport.requests import Request

def get_gcp_access_token():
    try:
        credentials, project_id = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        return credentials.token
    except Exception as e:
        print(f"Auth error via ADC: {e}")
        import subprocess
        return subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()

def fetch_and_save_session(session_id: str, output_file: str = "session_output.json"):
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "esmeralda-root-agent-918f")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = "35393829053923328"

    query_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}:query"

    token = get_gcp_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "class_method": "async_get_session",
        "input": {
            "user_id": "test-user-123",
            "session_id": session_id
        }
    }

    print(f"🔍 Fetching session {session_id} from Vertex AI Agent Engine...")
    resp = httpx.post(query_url, json=payload, headers=headers, timeout=30.0)

    if resp.status_code == 200:
        session_data = resp.json().get("output", {})
        with open(output_file, "w") as f:
            json.dump(session_data, f, indent=2)
        print(f"✅ Session successfully saved locally to: {output_file}")
    else:
        print(f"❌ Failed to fetch session (Status {resp.status_code}): {resp.text}")
        sys.exit(1)

if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "88b0a9df-6d0c-43f1-a53c-0e7d7dbb45ed"
    fetch_and_save_session(sid)
