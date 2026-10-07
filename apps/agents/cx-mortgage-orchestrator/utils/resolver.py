# Copyright 2026 Google LLC
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

import os

def resolve_agent_endpoint(target_agent_name: str) -> str:
    """
    Dynamically resolves the RPC target URL for a given agent name.

    Supports dual routing modes to ensure complete gateway agnosticism:
    - 'path' (Default for Apigee / Universal Gateways): https://<gateway_base>/agents/<agent_name>/api/a2a
    - 'host' (Kong / DNS Mesh): https://<agent_name>.esmeralda.internal/api/a2a

    Environment Variables:
        GATEWAY_BASE_URL: Base URL of the gateway (default: "https://gateway.esmeralda.internal")
        GATEWAY_ROUTING_MODE: Routing scheme ('path' or 'host', default: 'path')
    """
    gateway_base = os.environ.get("GATEWAY_BASE_URL", "https://gateway.esmeralda.internal").rstrip("/")
    routing_mode = os.environ.get("GATEWAY_ROUTING_MODE", "path").lower()

    if routing_mode == "host":
        return f"https://{target_agent_name}.esmeralda.internal/api/a2a"

    # Canonical path-based routing for universal gateway portability (Apigee, Kong, NGINX, ALB)
    return f"{gateway_base}/agents/{target_agent_name}/api/a2a"
