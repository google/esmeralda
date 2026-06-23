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

"""MCP toolset connections for the mortgage assistant agent."""

import json
import logging
import os
import ssl
import urllib.request

import google.auth
import google.auth.transport.requests
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from agent import USER_AUTH_TOKEN_KEY

logger = logging.getLogger(__name__)

if os.environ.get("DISABLE_SSL_VERIFICATION") == "true":
    ssl._create_default_https_context = ssl._create_unverified_context
    logger.warning("SSL certificate verification has been disabled via environment variable.")

DEFAULT_DMS_MCP_URL = "https://dms.internal.ai-demo.gcp.sc-ccn.xyz/mcp"
DEFAULT_INCOME_VERIFICATION_URL = "https://income-verification.internal.ai-demo.gcp.sc-ccn.xyz/mcp"
DEFAULT_EMAIL_MCP_URL = "https://email.internal.ai-demo.gcp.sc-ccn.xyz/mcp"


def _get_oidc_token(audience: str) -> str:
    """Generate an OIDC ID token via the IAM Credentials API."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    access_token = credentials.token

    sa_email = getattr(credentials, "service_account_email", None) or "-"
    url = (
        f"https://iamcredentials.googleapis.com/v1/projects/-"
        f"/serviceAccounts/{sa_email}:generateIdToken"
    )
    req_body = json.dumps({"audience": audience, "includeEmail": True}).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())["token"]
    except Exception as e:
        logger.error("Failed to fetch OIDC token via IAM API: %s", e)
        raise


def _make_header_provider(mcp_url: str):
    """Factory that returns a header_provider for a given MCP server URL."""
    audience = mcp_url.replace("/mcp", "")

    def header_provider(context):
        """Provides service-to-service ID token and forwards user auth token."""
        headers = {}
        # Bypass OIDC token generation if running against local servers or in local mode
        if "localhost" in mcp_url or "127.0.0.1" in mcp_url or os.getenv("LOCAL_MODE") == "true":
            logger.info("Bypassing OIDC token generation for local MCP server: %s", mcp_url)
        else:
            try:
                id_token = _get_oidc_token(audience)
                headers["Authorization"] = f"Bearer {id_token}"
            except Exception as e:
                logger.error("Failed to generate OIDC token, continuing without it: %s", e)

        if context and context.state:
            user_token = context.state.get(USER_AUTH_TOKEN_KEY)
            if user_token:
                headers["User-Auth-Token"] = user_token
                logger.info("Forwarding user auth token to MCP server")

        return headers

    return header_provider


def create_mcp_toolsets():
    """Create all three MCP toolset instances with auth and user token forwarding."""
    dms_url = os.environ.get("DMS_MCP_URL", DEFAULT_DMS_MCP_URL)
    income_url = os.environ.get("INCOME_VERIFICATION_URL", DEFAULT_INCOME_VERIFICATION_URL)
    email_url = os.environ.get("EMAIL_MCP_URL", DEFAULT_EMAIL_MCP_URL)

    dms_toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=dms_url,
            timeout=30.0,
            sse_read_timeout=300.0,
        ),
        header_provider=_make_header_provider(dms_url),
    )

    income_toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=income_url,
            timeout=30.0,
            sse_read_timeout=300.0,
        ),
        header_provider=_make_header_provider(income_url),
    )

    email_toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=email_url,
            timeout=30.0,
            sse_read_timeout=300.0,
        ),
        header_provider=_make_header_provider(email_url),
    )

    return dms_toolset, income_toolset, email_toolset
