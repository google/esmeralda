# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Enterprise Locust Stress Test Suite for Esmeralda Multi-Agent Platform.
Features:
  1. Native GCP SDK OAuth Authentication (google.auth.default).
  2. Session Creation & Rotation (async_create_session).
  3. Sliding Window RPM Quota Tracker (90 RPM Vertex AI Reasoning Engine regional quota limit).
  4. SSE Stream Breakdown: Time to First Event (TTFE) & MCP Tool Latencies.
"""

import collections
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time

from locust import HttpUser, between, events, task

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("esmeralda_locust_loadtest")

# Try to load remote_agent_engine_id from env, fallback to parsing root .env file
remote_agent_engine_id = os.getenv("REMOTE_AGENT_ENGINE_ID")

if not remote_agent_engine_id:
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.env"))
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith("REMOTE_AGENT_ENGINE_ID="):
                    val = line.split("=", 1)[1].strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    remote_agent_engine_id = val
                    break

# Fallback default for Esmeralda Dev
if not remote_agent_engine_id:
    remote_agent_engine_id = "projects/esmeralda-root-agent-3a3d/locations/us-central1/reasoningEngines/864970954164404224"

match = re.search(
    r"projects/([^/]+)/locations/([^/]+)/reasoningEngines/([^/]+)",
    remote_agent_engine_id
)
if match:
    PROJECT_ID = match.group(1)
    LOCATION = match.group(2)
    ENGINE_ID = match.group(3)
else:
    raise ValueError(f"Invalid REMOTE_AGENT_ENGINE_ID format: {remote_agent_engine_id}")

BASE_URL = f"https://{LOCATION}-aiplatform.googleapis.com"

logger.info("Loaded Esmeralda Engine Config: Project=%s, Location=%s, Engine ID=%s", PROJECT_ID, LOCATION, ENGINE_ID)


# ------------------------------------------------------------------------------
# 1. Native GCP Authentication Resolution
# ------------------------------------------------------------------------------
def resolve_gcp_access_token() -> str:
    """Retrieves a Google Cloud OAuth2 access token for authenticating with Vertex AI."""
    try:
        import google.auth
        import google.auth.transport.requests

        logger.info("Resolving Google Cloud credentials natively via google.auth.default()...")
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        if credentials.token:
            logger.info("Successfully resolved GCP access token natively.")
            return credentials.token
    except Exception as e:
        logger.warning("Native GCP credential resolution failed: %s. Falling back to gcloud CLI...", e)

    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
        logger.info("Successfully fetched GCP access token via gcloud CLI.")
        return token
    except Exception as ex:
        logger.error("GCP access token resolution failed via gcloud fallback: %s", ex)
        # Fallback to _AUTH_TOKEN env if available
        auth_env = os.getenv("_AUTH_TOKEN")
        if auth_env:
            return auth_env
        raise RuntimeError("No valid Google Cloud credentials or _AUTH_TOKEN found.")


try:
    GCP_ACCESS_TOKEN = resolve_gcp_access_token()
except Exception as err:
    logger.critical("Critical Auth Initialization Failure: %s", err)
    GCP_ACCESS_TOKEN = None


# ------------------------------------------------------------------------------
# 3. Sliding Window RPM Quota Tracker (90 RPM Threshold Warning)
# ------------------------------------------------------------------------------
class SlidingWindowRPMTracker:
    """Tracks actual outbound HTTP requests to Vertex AI in a rolling 60-second window."""
    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self.timestamps = collections.deque()
        self.lock = threading.Lock()

    def add_request(self) -> int:
        now = time.time()
        with self.lock:
            self.timestamps.append(now)
            self._prune(now)
            return len(self.timestamps)

    def _prune(self, now: float):
        cutoff = now - self.window_seconds
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()


rpm_tracker = SlidingWindowRPMTracker()


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """Listens to Locust request events and monitors RPM quota threshold (90 RPM)."""
    if request_type in ("POST", "GET") and ("reasoningEngines" in name):
        current_rpm = rpm_tracker.add_request()
        if current_rpm >= 90:
            logger.error(
                "🚨 [QUOTA EXCEEDED] Sliding Window Rate: %d RPM in the last 60s! "
                "Exceeded the 90 RPM regional quota limit!", current_rpm
            )
        elif current_rpm >= 80:
            logger.warning(
                "⚠️ [TRAFFIC WARNING] Sliding Window Rate: %d RPM in the last 60s! "
                "(Quota Limit: 90 RPM - High risk of HTTP 429 Resource Exhausted)", current_rpm
            )
        elif current_rpm % 10 == 0:
            logger.info(
                "📊 [TRAFFIC MONITOR] Sliding Window Rate: %d RPM in last 60s. (Quota Limit: 90 RPM)", current_rpm
            )


# ------------------------------------------------------------------------------
# 2 & 4. Locust User Class with Session Rotation & SSE Latency Parsing
# ------------------------------------------------------------------------------
class EsmeraldaAgentUser(HttpUser):
    """Simulates realistic FinOps mortgage coordinator turns against Esmeralda Agent Platform."""

    wait_time = between(10, 20)  # Pacing between turns to remain within RPM quotas
    host = BASE_URL

    def on_start(self):
        """Prepares virtual user session on startup."""
        self.user_id = f"locust-finops-{random.randint(10000, 99999)}"
        self.session_id = None
        self.headers = {
            "Authorization": f"Bearer {GCP_ACCESS_TOKEN}" if GCP_ACCESS_TOKEN else "",
            "Content-Type": "application/json"
        }
        self.max_turns = random.randint(1, 5)
        self.turns_executed = 0
        self.team = random.choice([
            {"project_id": "fictional-team-alpha", "agent_name": "alpha-coordinator"},
            {"project_id": "fictional-team-beta", "agent_name": "beta-coordinator"}
        ])

    @task
    def execute_turn(self):
        """Executes a multi-turn session with TTFE & MCP tool latency tracking."""
        self.turns_executed += 1
        turn_str = f"Turn {self.turns_executed}"

        # 2. Session Creation (async_create_session) if session not initialized
        if self.session_id is None:
            session_url = f"/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:query"
            session_payload = {
                "class_method": "async_create_session",
                "input": {
                    "user_id": self.user_id,
                    "state": {"caller_context": self.team}
                }
            }
            session_start = time.time()
            try:
                with self.client.post(
                    session_url,
                    json=session_payload,
                    headers=self.headers,
                    catch_response=True,
                    name="/query async_create_session"
                ) as response:
                    if response.status_code != 200:
                        response.failure(f"Session Creation HTTP {response.status_code}: {response.text}")
                        self.turns_executed -= 1
                        return

                    resp_json = response.json()
                    self.session_id = resp_json.get("output", {}).get("id")
                    if not self.session_id:
                        response.failure(f"Session Creation returned empty ID: {resp_json}")
                        self.turns_executed -= 1
                        return

                    session_latency = (time.time() - session_start) * 1000
                    events.request.fire(
                        request_type="AgentSession",
                        name="0. Create Session Latency",
                        response_time=session_latency,
                        response_length=len(response.text),
                        exception=None
                    )
                    response.success()
            except Exception as e:
                self.turns_executed -= 1
                return

        # Sample Mortgage FinOps Queries
        queries = [
            "Can you verify Julian Sterling's income?",
            "I'm reviewing the Rivera family's $700K loan application. Can you summarize their W2 earnings?",
            "Verify employment status and income for City General Hospital employees.",
            "Calculate debt-to-income ratio for application #88214."
        ]
        query = random.choice(queries)

        query_payload = {
            "class_method": "async_stream_query",
            "input": {
                "message": query,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "caller_context": self.team
            }
        }

        stream_url = f"/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"

        # 4. SSE Breakdown: TTFE & MCP Tool Latencies
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
                catch_response=True,
                name="/streamQuery async_stream_query"
            ) as response:
                if response.status_code != 200:
                    response.failure(f"HTTP {response.status_code}: {response.text}")
                else:
                    try:
                        for line in response.iter_lines():
                            if not line:
                                continue

                            bytes_read += len(line)
                            decoded_line = line.decode("utf-8").strip()

                            # Record Time to First Event (TTFE)
                            if ttfe is None:
                                ttfe = (time.time() - start_time) * 1000
                                events.request.fire(
                                    request_type="AgentSSE",
                                    name="1. Time to First Event (TTFE) - Overall",
                                    response_time=ttfe,
                                    response_length=0,
                                    exception=None
                                )
                                events.request.fire(
                                    request_type="AgentSSE",
                                    name=f"1. Time to First Event (TTFE) - {turn_str}",
                                    response_time=ttfe,
                                    response_length=0,
                                    exception=None
                                )

                            # Extract & Parse Function Calls / Responses
                            try:
                                event_json = json.loads(decoded_line)
                                if isinstance(event_json, dict):
                                    content = event_json.get("content", {})
                                    parts = content.get("parts", []) if isinstance(content, dict) else []

                                    for part in parts:
                                        if not isinstance(part, dict):
                                            continue

                                        if "function_call" in part:
                                            tool_name = part["function_call"].get("name", "unknown_tool")
                                            tool_start_time = time.time()

                                        elif "function_response" in part:
                                            if tool_start_time is not None:
                                                tool_latency = (time.time() - tool_start_time) * 1000
                                                events.request.fire(
                                                    request_type="AgentSSE",
                                                    name=f"2. Tool Latency ({tool_name})",
                                                    response_time=tool_latency,
                                                    response_length=0,
                                                    exception=None
                                                )
                                                tool_start_time = None
                            except (json.JSONDecodeError, Exception):
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
                            name=f"3. Total Turn Latency - {turn_str}",
                            response_time=total_latency,
                            response_length=bytes_read,
                            exception=None
                        )
                        response.success()
                    except Exception as e:
                        response.failure(f"Stream interrupted: {e}")
        finally:
            # Session rotation upon reaching max turns
            if self.turns_executed >= self.max_turns:
                self.user_id = f"locust-finops-{random.randint(10000, 99999)}"
                self.session_id = None
                self.max_turns = random.randint(1, 5)
                self.turns_executed = 0
