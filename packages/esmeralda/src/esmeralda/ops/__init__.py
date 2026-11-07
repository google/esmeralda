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

"""Read-only operations on an Esmeralda checkout, for agents (``esmeralda mcp``) and scripts.

Every function returns a small JSON-serialisable dict, never raw Terraform or log dumps:

* ``repo``: repository root, environments, units, state outputs, subprocess helpers, error hints;
* ``infra``: ``list_units``, ``status`` and ``plan_unit`` (read-only plan summary);
* ``logs``: ``agent_logs`` (Cloud Logging, tracebacks collapsed, repeats deduplicated);
* ``runtime``: ``query_agent`` (Agent Runtime) and ``test_gateway`` (``scripts/test_gateway.sh``);
* ``contracts``: ``describe_contracts`` (gateway adapter contract and ``gateways`` blocks);
* ``server``: the MCP server (stdio) exposing all of the above as tools.

Nothing here applies, destroys or writes cloud resources.
"""

from esmeralda.ops.repo import OpsError

__all__ = ["OpsError"]
