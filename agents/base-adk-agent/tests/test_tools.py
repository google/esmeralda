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

"""Tests for mortgage agent utility tools."""

from __future__ import annotations

import os
from unittest import mock

from agent.tools import list_mcp_connections


class TestListMcpConnections:
    def test_default_urls(self):
        result = list_mcp_connections()
        assert result["count"] == 3
        connections = result["connections"]
        assert connections[0]["tool_name_prefix"] == "dms_"
        assert connections[1]["tool_name_prefix"] == "income_"
        assert connections[2]["tool_name_prefix"] == "email_"
        assert all(c["status"] == "configured" for c in connections)

    def test_custom_urls_from_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "DMS_MCP_URL": "https://custom-dms/mcp",
                "INCOME_VERIFICATION_URL": "https://custom-income/mcp",
                "EMAIL_MCP_URL": "https://custom-email/mcp",
            },
        ):
            result = list_mcp_connections()
            connections = result["connections"]
            assert connections[0]["url"] == "https://custom-dms/mcp"
            assert connections[1]["url"] == "https://custom-income/mcp"
            assert connections[2]["url"] == "https://custom-email/mcp"
