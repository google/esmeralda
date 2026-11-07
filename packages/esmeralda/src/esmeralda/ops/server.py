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

"""``esmeralda mcp``: a read-only MCP server (stdio) to operate an Esmeralda checkout.

Tools return compact JSON. There is no apply or destroy tool: changes go through ``make`` and
review. Requires ``fastmcp`` (locked in the workspace through the MCP services): run it from the
repo root with ``uv run --frozen --all-packages esmeralda mcp``.
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from esmeralda.ops import contracts, infra, logs, repo, runtime
from esmeralda.ops.repo import OpsError

NAME = "esmeralda-ops"
INSTRUCTIONS = """Read-only operations for an Esmeralda deployment (Terragrunt environments under
infrastructure/live/<env>). Start with status(env) or list_units(env). plan_unit is read-only
(-lock=false). query_agent calls the deployed agent on Agent Runtime. There are no apply/destroy
tools: suggest the matching `make` target to the user instead."""

# Per-tool timeouts (seconds); the inner subprocess timeouts are a little shorter.
TIMEOUTS = {
    "status": 180,
    "list_units": 180,
    "plan_unit": 960,
    "query_agent": 330,
    "test_gateway": 330,
    "agent_logs": 120,
    "describe_contracts": 30,
}

_READ_ONLY = {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": True}

# Module level (not inside build_server) so the tool signatures resolve under `from __future__ import annotations`.
Env = Annotated[str, Field(description="environment directory under infrastructure/live, e.g. dev or prd")]
Agent = Annotated[str, Field(description="agent directory under apps/agents, e.g. cx-mortgage-orchestrator")]


def _missing_fastmcp() -> OpsError:
    return OpsError(
        "esmeralda mcp needs fastmcp, which is locked in the workspace but not installed in this environment. "
        "Run it from the repo root with: uv run --frozen --all-packages esmeralda mcp"
    )


def build_server(root: Path | None = None) -> Any:
    """The FastMCP server. ``root`` pins the repository (default: ESMERALDA_REPO or the cwd's checkout)."""
    try:
        from fastmcp import FastMCP
        from fastmcp.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise _missing_fastmcp() from exc

    def repo_root() -> Path:
        return root or repo.repo_root()

    def guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
        """OpsError (and any other failure) -> ToolError with an actionable message."""
        import inspect

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except OpsError as exc:
                    raise ToolError(str(exc)) from exc
                except Exception as exc:
                    raise ToolError(_unexpected(exc)) from exc

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except OpsError as exc:
                raise ToolError(str(exc)) from exc
            except Exception as exc:
                raise ToolError(_unexpected(exc)) from exc

        return wrapper

    server = FastMCP(NAME, instructions=INSTRUCTIONS, mask_error_details=False)

    def tool(name: str, **annotations: Any) -> Callable[[Callable[..., Any]], Any]:
        return server.tool(
            name=name,
            timeout=TIMEOUTS[name],
            annotations=ToolAnnotations(**{**_READ_ONLY, **annotations}),
            output_schema=None,  # plain JSON text only: no duplicate structuredContent tokens
        )

    @tool("status")
    @guarded
    def status(
        env: Env,
        unit: Annotated[
            str | None, Field(description="optional unit path for its full (non-sensitive) outputs")
        ] = None,
    ) -> dict[str, Any]:
        """Deployed units and key outputs: entry point IP/domain, gateways and routes, agent engine ids,
        MCP service URIs, test VM. With `unit`, that unit's outputs instead."""
        return infra.status(repo_root(), env, unit)

    @tool("list_units")
    @guarded
    def list_units(env: Env) -> dict[str, Any]:
        """Terragrunt units of an environment with deployed flag and managed resource count."""
        return infra.list_units(repo_root(), env)

    @tool("plan_unit")
    @guarded
    def plan_unit(
        env: Env,
        unit: Annotated[str, Field(description="unit path relative to the env, e.g. layer-5-workloads/entrypoint")],
    ) -> dict[str, Any]:
        """Read-only terragrunt plan of one unit (-lock=false, plan file under .esmeralda/plans): counts,
        one line per changed resource (+ create, ~ update, -/+ replace, - destroy) with changed attributes."""
        return infra.plan_unit(repo_root(), env, unit, timeout=TIMEOUTS["plan_unit"] - 60)

    @tool("query_agent", idempotent_hint=False)
    @guarded
    async def query_agent(
        env: Env,
        agent: Agent,
        message: Annotated[str, Field(description="the user message")],
    ) -> dict[str, Any]:
        """Query the agent deployed on Agent Runtime (same path as `make query TARGET=remote`): answer,
        tool calls, tokens, time and passed/failed."""
        return await runtime.query_agent(repo_root(), env, agent, message, timeout=TIMEOUTS["query_agent"] - 30)

    @tool("test_gateway")
    @guarded
    def test_gateway(env: Env) -> dict[str, Any]:
        """Probe every gateway route host through the entry point from the test VM
        (scripts/test_gateway.sh): per-host probe kind, HTTP code and ok."""
        return runtime.test_gateway(repo_root(), env, timeout=TIMEOUTS["test_gateway"] - 30)

    @tool("agent_logs")
    @guarded
    def agent_logs(
        env: Env,
        agent: Agent,
        since: Annotated[str, Field(description="look-back window: 45s, 30m, 2h, 1d")] = "30m",
        severity: Annotated[str, Field(description="minimum severity, e.g. INFO, WARNING, ERROR")] = "WARNING",
        limit: Annotated[int, Field(ge=1, le=200, description="max distinct entries")] = 50,
    ) -> dict[str, Any]:
        """Cloud Logging entries of the agent's Reasoning Engine, newest first: tracebacks collapsed to
        their last line, repeats deduplicated with a count, messages cut to 300 characters."""
        return logs.agent_logs(
            repo_root(), env, agent, since=since, severity=severity, limit=limit, timeout=TIMEOUTS["agent_logs"] - 20
        )

    @tool("describe_contracts", open_world_hint=False)
    @guarded
    def describe_contracts() -> dict[str, Any]:
        """The gateway adapter contract and the env.yaml `gateways` block (from the docs), plus each
        environment's current gateways block and gateway units."""
        return contracts.describe_contracts(repo_root())

    return server


def _unexpected(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    found = repo.hint(text)
    return f"{repo.short(text)}{f' ({found})' if found else ''}"


def main(repo_path: str | None = None) -> int:
    # stdout is the MCP channel: everything else goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        root = repo.checkout(repo_path) if repo_path else None
        if root is None:
            repo.repo_root()  # fail fast with a clear message when not in a checkout
        server = build_server(root)
    except OpsError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    server.run(transport="stdio", show_banner=False)
    return 0
