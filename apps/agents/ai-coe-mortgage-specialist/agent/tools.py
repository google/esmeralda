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

"""MCP tool servers of the mortgage assistant, reached through the gateway.

esmeralda.mcp.toolset adds the ID token for each server, the user's token (when the call carries
one) and the gateway API key. Local runs point the URLs at localhost (agent.yaml local_env).
"""

import os

from esmeralda import mcp

dms_url = os.environ.get("DMS_MCP_URL", "https://legacy-dms.esmeralda.internal/mcp")
income_url = os.environ.get("INCOME_VERIFICATION_URL", "https://income-verification.esmeralda.internal/mcp")
email_url = os.environ.get("EMAIL_MCP_URL", "https://corporate-email.esmeralda.internal/mcp")

dms_toolset = mcp.toolset(dms_url, prefix="dms")
income_toolset = mcp.toolset(income_url, prefix="income")
email_toolset = mcp.toolset(email_url, prefix="email")
