# Copyright 2025 Google LLC
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

import json
import logging
import os
import random
import time

from locust import HttpUser, between, task

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Try to load remote_agent_engine_id from env, fallback to parsing root .env file
remote_agent_engine_id = os.getenv("REMOTE_AGENT_ENGINE_ID")

if not remote_agent_engine_id:
    # Try to parse the root .env file manually (to avoid python-dotenv requirement)
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

if not remote_agent_engine_id:
    raise ValueError("REMOTE_AGENT_ENGINE_ID not found in environment or root .env file.")

parts = remote_agent_engine_id.split("/")
project_id = parts[1]
location = parts[3]
engine_id = parts[5]

# Convert remote agent engine ID to streaming URL.
base_url = f"https://{location}-aiplatform.googleapis.com"
url_path = f"/v1/projects/{project_id}/locations/{location}/reasoningEngines/{engine_id}:streamQuery"

logger.info("Using remote agent engine ID: %s", remote_agent_engine_id)
logger.info("Using base URL: %s", base_url)
logger.info("Using URL path: %s", url_path)


class ChatStreamUser(HttpUser):
    """Simulates a user interacting with the chat stream API."""

    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks
    host = base_url  # Set the base host URL for Locust

    @task
    def chat_stream(self) -> None:
        """Simulates a chat stream interaction."""
        headers = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {os.environ['_AUTH_TOKEN']}"

        # Simulate callers from two fictional FinOps teams
        team = random.choice([
            {"project_id": "fictional-team-alpha", "agent_name": "alpha-coordinator"},
            {"project_id": "fictional-team-beta", "agent_name": "beta-coordinator"}
        ])

        data = {
            "class_method": "async_stream_query",
            "input": {
                "user_id": "test",
                "message": "I'm reviewing the Rivera family's $700K loan. Can you summarize their 2024 tax returns?",
                "caller_context": team
            },
        }

        start_time = time.time()
        with self.client.post(
            url_path,
            headers=headers,
            json=data,
            catch_response=True,
            name="/streamQuery async_stream_query",
            stream=True,
            params={"alt": "sse"},
        ) as response:
            if response.status_code == 200:
                events = []
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode("utf-8")
                        events.append(line_str)

                        if "429 Too Many Requests" in line_str:
                            self.environment.events.request.fire(
                                request_type="POST",
                                name=f"{url_path} rate_limited 429s",
                                response_time=0,
                                response_length=len(line),
                                response=response,
                                context={},
                            )
                end_time = time.time()
                total_time = end_time - start_time
                self.environment.events.request.fire(
                    request_type="POST",
                    name="/streamQuery end",
                    response_time=total_time * 1000,  # Convert to milliseconds
                    response_length=len(events),
                    response=response,
                    context={},
                )
            else:
                response.failure(f"Unexpected status code: {response.status_code}")
