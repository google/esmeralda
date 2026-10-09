# Copyright 2025 Google LLC
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

"""Tests for remote agent metadata provider and auth header injection."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agent.remote_agent import _a2a_metadata_provider, _add_auth_header, USER_AUTH_TOKEN_KEY


CALLER = {"caller_context": {"project_id": "cx-project", "agent_name": "cx_mortgage_orchestrator"}}


class TestA2aMetadataProvider:
    @pytest.fixture(autouse=True)
    def identity(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cx-project")
        monkeypatch.setenv("AGENT_NAME", "cx_mortgage_orchestrator")

    def test_attaches_token_from_session_state(self):
        ctx = MagicMock()
        ctx.session.state = {USER_AUTH_TOKEN_KEY: "test-token-123"}
        metadata = _a2a_metadata_provider(ctx, MagicMock())
        assert metadata == {**CALLER, USER_AUTH_TOKEN_KEY: "test-token-123"}

    def test_only_caller_context_when_no_token(self):
        ctx = MagicMock()
        ctx.session.state = {}
        assert _a2a_metadata_provider(ctx, MagicMock()) == CALLER

    def test_only_caller_context_when_no_session(self):
        ctx = MagicMock()
        ctx.session = None
        assert _a2a_metadata_provider(ctx, MagicMock()) == CALLER

    def test_only_caller_context_when_no_state(self):
        ctx = MagicMock()
        ctx.session.state = None
        assert _a2a_metadata_provider(ctx, MagicMock()) == CALLER


class TestAddAuthHeader:
    def test_injects_bearer_token(self):
        mock_creds = MagicMock()
        mock_creds.token = "access-token-abc"

        with patch("agent.remote_agent.google.auth.default", return_value=(mock_creds, "project-id")):
            request = MagicMock()
            request.headers = {}
            asyncio.run(_add_auth_header(request))
            assert request.headers["Authorization"] == "Bearer access-token-abc"
            mock_creds.refresh.assert_called_once()
