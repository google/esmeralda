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

"""Utility tool functions for the mortgage assistant agent."""

from __future__ import annotations

import os

DEFAULT_DMS_MCP_URL = "https://dms.internal.ai-demo.gcp.sc-ccn.xyz/mcp"
DEFAULT_INCOME_VERIFICATION_URL = "https://income-verification.internal.ai-demo.gcp.sc-ccn.xyz/mcp"
DEFAULT_EMAIL_MCP_URL = "https://email.internal.ai-demo.gcp.sc-ccn.xyz/mcp"


def list_mcp_connections() -> dict:
    """List all configured MCP server connections.

    Returns:
        Dictionary with a list of MCP server names, URLs, and prefixes.
    """
    dms_mcp_url = os.environ.get("DMS_MCP_URL", DEFAULT_DMS_MCP_URL)
    income_verification_url = os.environ.get("INCOME_VERIFICATION_URL", DEFAULT_INCOME_VERIFICATION_URL)
    email_mcp_url = os.environ.get("EMAIL_MCP_URL", DEFAULT_EMAIL_MCP_URL)
    connections = [
        {
            "name": "Document Management System",
            "tool_name_prefix": "dms_",
            "url": dms_mcp_url,
            "status": "configured",
        },
        {
            "name": "Income Verification Service",
            "tool_name_prefix": "income_",
            "url": income_verification_url,
            "status": "configured",
        },
        {
            "name": "Corporate Email System",
            "tool_name_prefix": "email_",
            "url": email_mcp_url,
            "status": "configured",
        },
    ]
    return {"connections": connections, "count": len(connections)}
