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

"""Locust load testing script for Esmeralda Root Agent on Vertex AI Reasoning Engines."""

import os
import time
import random
import requests
import google.auth
import google.auth.transport.requests
from locust import User, task, between, events

PROMPTS = [
    "Can you verify Julian Sterling's income?",
    "Check mortgage application status for Julian Sterling.",
    "Verify employment details for Julian A. Sterling at City General Hospital."
]

class RootAgentLocustUser(User):
    """Simulates concurrent users querying the remote Root Agent on Vertex AI."""
    wait_time = between(1.0, 3.0)

    def on_start(self):
        """Authenticates with GCP and builds Reasoning Engine API endpoints."""
        try:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_request = google.auth.transport.requests.Request()
            credentials.refresh(auth_request)
            self.token = credentials.token
        except Exception as e:
            print(f"❌ Locust Auth Error: {e}")
            self.token = ""

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        self.project_id = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-dev")
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.resource_id = os.getenv("ROOT_REASONING_ENGINE_ID", "1234567890123456789")
        self.base_url = f"https://{self.location}-aiplatform.googleapis.com/v1beta1/projects/{self.project_id}/locations/{self.location}/reasoningEngines/{self.resource_id}"

    @task
    def stream_query_root_agent(self):
        """Sends a query request to the Reasoning Engine streamQuery endpoint."""
        prompt = random.choice(PROMPTS)
        user_id = f"locust-user-{random.randint(100, 999)}"
        
        url = f"{self.base_url}:streamQuery?alt=sse"
        payload = {
            "class_method": "async_stream_query",
            "input": {
                "user_id": user_id,
                "message": prompt
            }
        }

        start_time = time.time()
        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=60.0)
            total_time_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                events.request.fire(
                    request_type="POST",
                    name="streamQuery",
                    response_time=total_time_ms,
                    response_length=len(response.content),
                    exception=None,
                )
            else:
                events.request.fire(
                    request_type="POST",
                    name="streamQuery",
                    response_time=total_time_ms,
                    response_length=0,
                    exception=Exception(f"HTTP {response.status_code}: {response.text[:200]}"),
                )
        except Exception as e:
            total_time_ms = (time.time() - start_time) * 1000
            events.request.fire(
                request_type="POST",
                name="streamQuery",
                response_time=total_time_ms,
                response_length=0,
                exception=e,
            )
