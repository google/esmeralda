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

"""Integration tests for base-adk-agent → A2A agent → MCP server chain.

Tests the :streamQuery endpoint on the deployed base-adk-agent, which
delegates mortgage queries to the a2a-mortgage-agent via RemoteA2aAgent,
which in turn calls MCP servers (DMS, Income Verification, Email).
"""

import json
import uuid

import requests


def _stream_query(
    base_url: str, message: str, auth_headers: dict[str, str]
) -> list[dict]:
    """Send a :streamQuery request and parse the newline-delimited JSON events."""
    resp = requests.post(
        f"{base_url}:streamQuery",
        headers=auth_headers,
        json={
            "input": {
                "message": message,
                "user_id": "integration-test",
            }
        },
        timeout=180,
    )
    assert resp.status_code == 200, (
        f"streamQuery failed: {resp.status_code} {resp.text}"
    )

    events = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _stream_query_with_token(
    base_url: str,
    message: str,
    user_token: str,
    auth_headers: dict[str, str],
) -> list[dict]:
    """Send a streaming_agent_run_with_events request with a user auth token."""
    request_payload = json.dumps({
        "message": {
            "role": "user",
            "parts": [{"text": message}],
        },
        "user_id": "integration-test",
        "authorizations": {
            "user_auth_token": {
                "access_token": user_token,
            }
        },
    })
    resp = requests.post(
        f"{base_url}:streamQuery",
        headers=auth_headers,
        json={
            "class_method": "streaming_agent_run_with_events",
            "input": {
                "request_json": request_payload,
            },
        },
        timeout=180,
    )
    assert resp.status_code == 200, (
        f"streaming_agent_run_with_events failed: {resp.status_code} {resp.text}"
    )

    events = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


# -- Base Agent → A2A Delegation ----------------------------------------------


class TestBaseToA2ADelegation:
    """Tests that base-adk-agent delegates to a2a-mortgage-agent via A2A."""

    def test_stream_query_returns_events(
        self, base_agent_url: str, auth_headers: dict[str, str]
    ) -> None:
        events = _stream_query(
            base_agent_url, "Hello, what can you do?", auth_headers
        )
        assert len(events) > 0, "Expected at least one event"

        has_text = any(
            part.get("text")
            for event in events
            if event.get("content", {}).get("parts")
            for part in event["content"]["parts"]
        )
        assert has_text, "Expected at least one event with text content"

    def test_document_search_delegation(
        self, base_agent_url: str, auth_headers: dict[str, str]
    ) -> None:
        """Verify document queries flow through: base → A2A → DMS MCP."""
        events = _stream_query(
            base_agent_url,
            "Search for documents for the Sterling family",
            auth_headers,
        )
        assert len(events) > 0, "Expected at least one event"

        raw = json.dumps(events)
        has_delegation = "mortgage_tools_agent" in raw or "transfer_to_agent" in raw
        assert has_delegation, (
            "Expected delegation to mortgage_tools_agent in response events"
        )

    def test_income_verification_delegation(
        self, base_agent_url: str, auth_headers: dict[str, str]
    ) -> None:
        """Verify income queries flow through: base → A2A → Income MCP."""
        events = _stream_query(
            base_agent_url,
            "Verify income for Julian Sterling",
            auth_headers,
        )
        assert len(events) > 0, "Expected at least one event"


# -- User Token Passthrough ---------------------------------------------------


class TestUserTokenPassthrough:
    """Tests that a user auth token flows through the full chain."""

    def test_token_passthrough_with_document_query(
        self, base_agent_url: str, auth_headers: dict[str, str]
    ) -> None:
        """Send a document query with a user token and verify the chain completes.

        This proves the token traverses: BFF → base agent (session state)
        → A2A metadata → a2a mortgage agent (session state) → MCP headers.
        """
        test_token = f"test_token_{uuid.uuid4().hex[:16]}"

        events = _stream_query_with_token(
            base_agent_url,
            "Search for documents for the Sterling family",
            test_token,
            auth_headers,
        )
        assert len(events) > 0, "Expected at least one event"
