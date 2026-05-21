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

"""Shared fixtures for integration tests against deployed Agent Engine agents."""

import os
import subprocess

import pytest
import requests

REGION = os.getenv("REGION", "us-central1")


def _get_project_id() -> str:
    """Get project ID from env var, .env file, or gcloud config."""
    project_id = os.getenv("PROJECT_ID")
    if project_id:
        return project_id
    env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("PROJECT_ID="):
                    return line.split("=", 1)[1].strip().strip('"')
    return subprocess.check_output(
        ["gcloud", "config", "get-value", "project"], text=True
    ).strip()


def _get_project_number() -> str:
    """Get project number from env var or by looking up the project ID."""
    project_number = os.getenv("PROJECT_NUMBER")
    if project_number:
        return project_number
    project_id = _get_project_id()
    return subprocess.check_output(
        ["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"],
        text=True,
    ).strip()


def _find_engine_by_display_name(
    display_name: str, access_token: str
) -> str:
    """Look up a Reasoning Engine resource name by its display name."""
    project_number = _get_project_number()
    api_base = (
        f"https://{REGION}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{project_number}/locations/{REGION}"
    )
    resp = requests.get(
        f"{api_base}/reasoningEngines",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"Failed to list reasoning engines: {resp.status_code} {resp.text}"
    )
    for engine in resp.json().get("reasoningEngines", []):
        if engine.get("displayName") == display_name:
            resource = engine["name"]
            return f"https://{REGION}-aiplatform.googleapis.com/v1beta1/{resource}"

    available = [
        e.get("displayName")
        for e in resp.json().get("reasoningEngines", [])
    ]
    raise LookupError(
        f"No engine with displayName={display_name!r}. "
        f"Available: {available}"
    )


@pytest.fixture(scope="session")
def access_token() -> str:
    """Get a Google Cloud access token for Vertex AI API calls."""
    return subprocess.check_output(
        ["gcloud", "auth", "print-access-token"],
        text=True,
    ).strip()


@pytest.fixture(scope="session")
def auth_headers(access_token: str) -> dict[str, str]:
    """HTTP headers with access token auth for Agent Engine API calls."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="session")
def a2a_base_url(access_token: str) -> str:
    """Base A2A URL for the mortgage agent, discovered by display name.

    Override with env var A2A_AGENT_URL to skip discovery.
    """
    override = os.getenv("A2A_AGENT_URL")
    if override:
        return override
    engine_url = _find_engine_by_display_name("a2a-mortgage-agent", access_token)
    return f"{engine_url}/a2a"


@pytest.fixture(scope="session")
def base_agent_url(access_token: str) -> str:
    """Base URL for the base-adk-agent, discovered by display name.

    Override with env var BASE_AGENT_URL to skip discovery.
    """
    override = os.getenv("BASE_AGENT_URL")
    if override:
        return override
    return _find_engine_by_display_name("base-adk-agent", access_token)
