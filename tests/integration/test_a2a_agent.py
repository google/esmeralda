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

"""Integration tests for the A2A mortgage agent deployed on Agent Engine.

The Agent Engine A2A endpoint uses protobuf-based REST:
- Card at {base}/v1/card
- Messages at POST {base}/v1/message:send
- Body: {"request": {"role": "ROLE_USER", "content": [{"text": "..."}]}}
- Returns TASK_STATE_SUBMITTED; poll GET {base}/v1/tasks/{id} for result
"""

import json
import time

import requests


POLL_INTERVAL = 3
POLL_MAX_RETRIES = 30


def _send_and_poll(
    base_url: str, text: str, auth_headers: dict[str, str]
) -> dict:
    """Send an A2A message and poll until the task completes.

    Agent Engine uses InMemoryTaskStore with multiple container instances.
    Poll requests may hit a different instance than the one processing the task,
    returning "Task not found" intermittently. We retry through those.
    """
    resp = requests.post(
        f"{base_url}/v1/message:send",
        headers=auth_headers,
        json={
            "request": {
                "role": "ROLE_USER",
                "content": [{"text": text}],
            }
        },
        timeout=60,
    )
    assert resp.status_code == 200, (
        f"message:send failed: {resp.status_code} {resp.text}"
    )
    send_result = resp.json()
    task_id = send_result["task"]["id"]

    for _ in range(POLL_MAX_RETRIES):
        time.sleep(POLL_INTERVAL)
        poll_resp = requests.get(
            f"{base_url}/v1/tasks/{task_id}",
            headers=auth_headers,
            timeout=30,
        )
        if poll_resp.status_code != 200:
            continue
        task = poll_resp.json()
        state = task.get("status", {}).get("state", "")
        if state == "TASK_STATE_COMPLETED":
            return task
        if state == "TASK_STATE_FAILED":
            raise AssertionError(
                f"Task failed: {json.dumps(task, indent=2)}"
            )

    raise AssertionError(
        f"Task {task_id} did not complete within "
        f"{POLL_INTERVAL * POLL_MAX_RETRIES}s"
    )


# -- Agent Card ---------------------------------------------------------------


class TestAgentCard:
    """Tests for the /v1/card endpoint on Agent Engine."""

    def test_agent_card_returns_200(
        self, a2a_base_url: str, auth_headers: dict[str, str]
    ) -> None:
        resp = requests.get(
            f"{a2a_base_url}/v1/card",
            headers=auth_headers,
            timeout=30,
        )
        assert resp.status_code == 200

    def test_agent_card_structure(
        self, a2a_base_url: str, auth_headers: dict[str, str]
    ) -> None:
        resp = requests.get(
            f"{a2a_base_url}/v1/card",
            headers=auth_headers,
            timeout=30,
        )
        card = resp.json()

        assert card["name"] == "a2a-mortgage-agent"
        assert "capabilities" in card
        assert "skills" in card
        assert len(card["skills"]) == 3

    def test_agent_card_skills(
        self, a2a_base_url: str, auth_headers: dict[str, str]
    ) -> None:
        resp = requests.get(
            f"{a2a_base_url}/v1/card",
            headers=auth_headers,
            timeout=30,
        )
        skills = {s["id"]: s for s in resp.json()["skills"]}

        assert "document-search" in skills
        assert "income-verification" in skills
        assert "corporate-email" in skills
        assert "dms" in skills["document-search"]["tags"]
        assert "income" in skills["income-verification"]["tags"]
        assert "email" in skills["corporate-email"]["tags"]


# -- A2A Message Send (A2A → MCP) --------------------------------------------


class TestA2AMessageSend:
    """Tests for sending A2A messages that trigger MCP tool calls."""

    def test_document_search_completes(
        self, a2a_base_url: str, auth_headers: dict[str, str]
    ) -> None:
        task = _send_and_poll(
            a2a_base_url,
            "Search for documents for the Sterling family",
            auth_headers,
        )
        artifacts = task.get("artifacts", [])
        assert len(artifacts) > 0, "Expected at least one artifact"

        has_text = any(
            part.get("text")
            for artifact in artifacts
            for part in artifact.get("parts", [])
        )
        assert has_text, (
            f"Expected at least one text part in artifacts: "
            f"{json.dumps(artifacts, indent=2)}"
        )

    def test_income_verification_completes(
        self, a2a_base_url: str, auth_headers: dict[str, str]
    ) -> None:
        task = _send_and_poll(
            a2a_base_url,
            "Verify income for Julian Sterling",
            auth_headers,
        )
        assert task.get("status", {}).get("state") == "TASK_STATE_COMPLETED"

    def test_email_read_completes(
        self, a2a_base_url: str, auth_headers: dict[str, str]
    ) -> None:
        task = _send_and_poll(
            a2a_base_url,
            "Read the corporate email inbox",
            auth_headers,
        )
        assert task.get("status", {}).get("state") == "TASK_STATE_COMPLETED"
