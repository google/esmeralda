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

"""
Locust multi-turn load testing script for Esmeralda Root Agent on Vertex AI Reasoning Engines.
Measures Time to First Event (TTFE), Turn N latencies, Tool Execution Latency, and Context Caching hits.
"""

import os
import sys
import time
import random
import json
import logging
import requests
from locust import HttpUser, task, between, events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("locust_root_agent")

def resolve_gcp_access_token() -> str:
    """Retrieves a Google Cloud OAuth2 access token for authenticating with Vertex AI."""
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        if credentials.token:
            return credentials.token
    except Exception as e:
        logger.warning(f"Native GCP credential resolution failed: {e}. Falling back to gcloud CLI...")

    import subprocess
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
    except Exception as ex:
        logger.error(f"GCP access token resolution failed via gcloud fallback: {ex}")
        raise RuntimeError("No valid Google Cloud credentials or gcloud CLI found.")

try:
    GCP_ACCESS_TOKEN = resolve_gcp_access_token()
except Exception as err:
    logger.critical(f"Critical Auth Initialization Failure: {err}")
    GCP_ACCESS_TOKEN = None

PROJECT = os.getenv("ROOT_AGENT_PROJECT_ID", "esmeralda-root-agent-dev")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID", "1234567890123456789")

# Multi-turn conversation trees for testing prompt caching across turns
CONVERSATION_FLOWS = [
    [
        "Can you verify Julian Sterling's income?",
        "What is his annual salary at City General Hospital?",
        "Can you also verify if his employment status is active?",
        "Calculate his monthly gross income based on that."
    ],
    [
        "What is the status of Julian Sterling's mortgage application?",
        "Does his income meet the threshold for a $400,000 loan?",
        "Thank you, please summarize his income verification for the underwriter."
    ]
]

class RootAgentMultiTurnUser(HttpUser):
    """
    Simulates a multi-turn user dialogue against the Vertex AI Reasoning Engine agent.
    Reuses session_id across turns to measure turn-level latency and prompt cache hits.
    """
    wait_time = between(5, 12)
    host = f"https://{LOCATION}-aiplatform.googleapis.com"

    def on_start(self):
        """Prepares virtual user session state and headers."""
        self.user_id = f"locust-multiturn-{random.randint(10000, 99999)}"
        self.session_id = None
        self.flow = random.choice(CONVERSATION_FLOWS)
        self.turns_executed = 0
        self.max_turns = len(self.flow)

        self.headers = {
            "Authorization": f"Bearer {GCP_ACCESS_TOKEN}" if GCP_ACCESS_TOKEN else "",
            "Content-Type": "application/json"
        }

    @task
    def execute_multi_turn_dialogue(self):
        """Executes a dialogue turn within the active session."""
        if self.turns_executed >= self.max_turns:
            # Rotate session after conversation flow is complete
            logger.info(f"Completed conversation flow for {self.user_id}. Resetting session.")
            self.user_id = f"locust-multiturn-{random.randint(10000, 99999)}"
            self.session_id = None
            self.flow = random.choice(CONVERSATION_FLOWS)
            self.turns_executed = 0
            self.max_turns = len(self.flow)

        # 1. Establish session on Turn 1
        if self.session_id is None:
            session_start = time.time()
            session_url = f"/v1beta1/projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}/sessions"
            session_payload = {
                "user_id": self.user_id
            }
            try:
                with self.client.post(
                    session_url,
                    json=session_payload,
                    headers=self.headers,
                    catch_response=True
                ) as response:
                    if response.status_code != 200:
                        response.failure(f"Session Creation HTTP {response.status_code}: {response.text}")
                        return
                    
                    data = response.json()
                    op_name = data.get("name", "")
                    parts = op_name.split("/")
                    self.session_id = parts[parts.index("sessions") + 1] if "sessions" in parts else parts[-1]
                    
                    session_latency = (time.time() - session_start) * 1000
                    events.request.fire(
                        request_type="AgentSession",
                        name="0. Create Control Plane Session",
                        response_time=session_latency,
                        response_length=len(response.text),
                        exception=None
                    )
                    response.success()
            except Exception as e:
                session_latency = (time.time() - session_start) * 1000
                events.request.fire(
                    request_type="AgentSession",
                    name="0. Create Control Plane Session",
                    response_time=session_latency,
                    response_length=0,
                    exception=e
                )
                logger.error(f"Failed session creation: {e}")
                return

            # 1b. Register session in ADK Runner SessionStore
            adk_create_url = f"/v1beta1/projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}:query"
            adk_create_payload = {
                "class_method": "async_create_session",
                "input": {
                    "user_id": self.user_id,
                    "session_id": self.session_id
                }
            }
            try:
                with self.client.post(
                    adk_create_url,
                    json=adk_create_payload,
                    headers=self.headers,
                    catch_response=True
                ) as reg_resp:
                    if reg_resp.status_code != 200:
                        reg_resp.failure(f"ADK Session Registration HTTP {reg_resp.status_code}: {reg_resp.text}")
                        self.session_id = None
                        return
                    reg_resp.success()
            except Exception as e:
                logger.error(f"Failed ADK session registration: {e}")
                self.session_id = None
                return

        # 2. Execute streaming turn query
        prompt = self.flow[self.turns_executed]
        self.turns_executed += 1
        turn_label = f"Turn {self.turns_executed}"

        query_payload = {
            "class_method": "async_stream_query",
            "input": {
                "user_id": self.user_id,
                "session_id": self.session_id,
                "message": prompt
            }
        }

        stream_url = f"/v1beta1/projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}:streamQuery?alt=sse"
        start_time = time.time()
        ttfe = None
        tool_start_time = None
        tool_name = None
        bytes_read = 0

        try:
            with self.client.post(
                stream_url,
                json=query_payload,
                headers=self.headers,
                stream=True,
                catch_response=True
            ) as response:
                if response.status_code != 200:
                    response.failure(f"HTTP Status {response.status_code}: {response.text}")
                else:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        bytes_read += len(line)

                        if ttfe is None:
                            ttfe = (time.time() - start_time) * 1000
                            events.request.fire(
                                request_type="AgentSSE",
                                name="1. TTFE - Overall",
                                response_time=ttfe,
                                response_length=0,
                                exception=None
                            )
                            events.request.fire(
                                request_type="AgentSSE",
                                name=f"1. TTFE - {turn_label}",
                                response_time=ttfe,
                                response_length=0,
                                exception=None
                            )

                        decoded_line = line.decode('utf-8').strip()
                        try:
                            if decoded_line.startswith("data:"):
                                decoded_line = decoded_line[5:].strip()

                            event_json = json.loads(decoded_line)
                            if isinstance(event_json, dict):
                                content = event_json.get("content", {})
                                parts = content.get("parts", []) if isinstance(content, dict) else []
                                for part in parts:
                                    if isinstance(part, dict):
                                        if "function_call" in part:
                                            tool_name = part["function_call"].get("name", "tool")
                                            tool_start_time = time.time()
                                        elif "function_response" in part and tool_start_time:
                                            tool_latency = (time.time() - tool_start_time) * 1000
                                            events.request.fire(
                                                request_type="AgentSSE",
                                                name=f"2. Tool Latency ({tool_name})",
                                                response_time=tool_latency,
                                                response_length=0,
                                                exception=None
                                            )
                                            tool_start_time = None
                        except Exception:
                            pass

                    total_latency = (time.time() - start_time) * 1000
                    events.request.fire(
                        request_type="AgentSSE",
                        name="3. Total Turn Latency - Overall",
                        response_time=total_latency,
                        response_length=bytes_read,
                        exception=None
                    )
                    events.request.fire(
                        request_type="AgentSSE",
                        name=f"3. Total Turn Latency - {turn_label}",
                        response_time=total_latency,
                        response_length=bytes_read,
                        exception=None
                    )
                    response.success()
        except Exception as e:
            total_latency = (time.time() - start_time) * 1000
            events.request.fire(
                request_type="AgentSSE",
                name="3. Total Turn Latency - Overall",
                response_time=total_latency,
                response_length=0,
                exception=e
            )
            logger.error(f"Stream error on {turn_label}: {e}")
