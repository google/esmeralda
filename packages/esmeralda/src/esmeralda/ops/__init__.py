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

"""Operations on an Esmeralda checkout, backing the ``esmeralda`` CLI and ``esmeralda mcp``.

Every function returns a small JSON-serialisable dict, never raw Terraform or log dumps:

* ``repo``: repository root, git/worktree status, doctor preflight checks, environments, units,
  state outputs, subprocess helpers, error hints;
* ``infra``: ``list_units``, ``status``, ``plan_unit`` (read-only plan summary), and ``apply_unit``
  (plan-first apply with destroy guard);
* ``logs``: ``agent_logs`` (Cloud Logging, tracebacks collapsed, repeats deduplicated);
* ``runtime``: ``query_agent`` (Agent Runtime), ``test_gateway`` (``scripts/test_gateway.sh``), and ``verify``;
* ``contracts``: ``describe_contracts`` (architectural contracts and ``gateways`` blocks);
* ``guard``: ``run_guard`` (public-repository sensitive-content scanner);
* ``server``: the read-only MCP server (stdio) exposing the read-only tools.
"""

from esmeralda.ops.repo import OpsError

__all__ = ["OpsError"]
