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

"""Infrastructure views: units, deployment status and read-only plan summaries.

``plan_unit`` runs ``terragrunt plan -lock=false -out=<.esmeralda/plans/...>`` and summarises
``terragrunt show -json`` of that plan file: counts, one line per changed resource, and the names
of the attributes that change. It never applies anything.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
from esmeralda.ops.repo import OpsError, UnitState

PLAN_DIR = Path(".esmeralda/plans")  # under the repo root; gitignored
MAX_PLAN_LINES = 60
_BLOB = 200  # scalar outputs longer than this are summarised, not returned


# ------------------------------------------------------------------------------------------
# units and status
# ------------------------------------------------------------------------------------------


def list_units(root: Path, env: str) -> dict[str, Any]:
    states = repo.all_states(root, env)
    return {
        "env": env,
        "backend": repo.backend(root, env),
        "units": [{"unit": s.unit, "deployed": s.deployed, "resources": s.resources} for s in states],
    }


def _by_prefix(states: list[UnitState], prefix: str) -> dict[str, UnitState]:
    return {s.unit[len(prefix) :]: s for s in states if s.unit.startswith(prefix) and s.deployed is not False}


def summarize_status(env: str, backend: str, states: list[UnitState]) -> dict[str, Any]:
    """The key outputs of an environment: entry point, gateways, agents, MCP services, test VM."""
    by_unit = {s.unit: s for s in states}

    def out(unit: str, name: str) -> Any:
        state = by_unit.get(unit)
        return state.outputs.get(name) if state else None

    layer1 = (by_unit.get("layer-1-projects") or _empty()).outputs
    projects = {k.removesuffix("_project_id"): v for k, v in layer1.items() if k.endswith("_project_id")}
    entry_point = {
        "ip": out("layer-2-networking", "entrypoint_address") or out("layer-5-workloads/entrypoint", "ip_address"),
        "domain": out("layer-2-networking", "entrypoint_domain"),
        "routing": out("layer-5-workloads/entrypoint", "routing"),
    }
    gateways = {}
    for name, state in _by_prefix(states, "layer-5-workloads/gateways/").items():
        routes = state.outputs.get("routes") or {}
        gateways[name] = {
            "backend_service": state.outputs.get("backend_service_id"),
            "routes": sorted(f"{r.get('host')} ({r.get('probe')})" for r in routes.values() if isinstance(r, dict))
            if isinstance(routes, dict)
            else routes,
        }
    agents = {
        name: {"engine_id": s.outputs.get("engine_id"), "endpoint_url": s.outputs.get("endpoint_url")}
        for name, s in _by_prefix(states, "layer-5-workloads/agents/").items()
    }
    services = {
        name: s.outputs.get("service_uri")
        for name, s in _by_prefix(states, "layer-5-workloads/services/").items()
        if s.outputs.get("service_uri")
    }
    vm = by_unit.get("layer-5-workloads/services/test-vm")
    result: dict[str, Any] = {
        "env": env,
        "backend": backend,
        "deployed": sum(1 for s in states if s.deployed),
        "units": len(states),
        "not_deployed": [s.unit for s in states if s.deployed is False],
        "unknown": [s.unit for s in states if s.deployed is None],
        "projects": projects,
        "entry_point": entry_point,
        "gateways": gateways,
        "agents": agents,
        "mcp_services": services,
        "agent_gateway": out("layer-4-governance", "agent_gateway_id"),
        "test_vm": {k: vm.outputs.get(k) for k in ("instance_name", "instance_zone")} if vm and vm.outputs else None,
    }
    return {k: v for k, v in result.items() if v not in (None, [], {})}


def _empty() -> UnitState:
    return UnitState("", None, None, {}, [])


def compact_outputs(state: UnitState) -> dict[str, Any]:
    """A unit's non-sensitive outputs, with long values (PEMs, big maps) replaced by a size note."""
    result = {}
    for name, value in state.outputs.items():
        text = json.dumps(value, default=str)
        result[name] = value if len(text) <= _BLOB else f"<{type(value).__name__}, {len(text)} chars>"
    return result


def status(root: Path, env: str, unit: str | None = None) -> dict[str, Any]:
    if unit:
        repo.unit_dir(root, env, unit)  # validate
        state = repo.unit_state(root, env, unit.strip().strip("/"))
        return {
            "env": env,
            "unit": state.unit,
            "deployed": state.deployed,
            "resources": state.resources,
            "outputs": compact_outputs(state),
            "sensitive_outputs": state.sensitive,
        }
    return summarize_status(env, repo.backend(root, env), repo.all_states(root, env))


# ------------------------------------------------------------------------------------------
# plan
# ------------------------------------------------------------------------------------------

_ACTIONS = {
    ("create",): ("+", "add"),
    ("update",): ("~", "change"),
    ("delete",): ("-", "destroy"),
    ("delete", "create"): ("-/+", "replace"),
    ("create", "delete"): ("+/-", "replace"),
    ("read",): ("<=", "read"),
}


def changed_attributes(change: dict[str, Any]) -> list[str]:
    """Top-level attribute names that differ between ``before`` and ``after`` (unknowns count)."""
    before = change.get("before") or {}
    after = change.get("after") or {}
    unknown = change.get("after_unknown") or {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    names = set()
    for key in set(before) | set(after) | set(unknown if isinstance(unknown, dict) else {}):
        if before.get(key) != after.get(key) or (isinstance(unknown, dict) and _has_unknown(unknown.get(key))):
            names.add(key)
    return sorted(names)


def _has_unknown(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, dict):
        return any(_has_unknown(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_unknown(v) for v in value)
    return False


def _replace_paths(change: dict[str, Any]) -> list[str]:
    paths = change.get("replace_paths") or []
    return sorted({str(p[0]) for p in paths if isinstance(p, list) and p})


def summarize_plan(plan: dict[str, Any], *, max_lines: int = MAX_PLAN_LINES) -> dict[str, Any]:
    """Counts and one line per changed resource from ``terraform show -json <planfile>``."""
    counts = {"add": 0, "change": 0, "destroy": 0, "replace": 0, "read": 0}
    lines = []
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        actions = tuple(change.get("actions") or ())
        if actions in (("no-op",), ()):
            continue
        symbol, kind = _ACTIONS.get(actions, ("?", "/".join(actions)))
        counts[kind] = counts.get(kind, 0) + 1
        line = f"{symbol} {rc.get('address')}"
        if kind in ("change", "replace"):
            attrs = changed_attributes(change)
            forces = _replace_paths(change)
            if attrs:
                line += ": " + ", ".join(attrs[:12]) + (f" (+{len(attrs) - 12})" if len(attrs) > 12 else "")
            if forces:
                line += f" [forces replacement: {', '.join(forces)}]"
        lines.append(line)
    order = {"-/+": 0, "+/-": 0, "-": 1, "~": 2, "+": 3, "<=": 4}
    lines.sort(key=lambda text: (order.get(text.split(" ", 1)[0], 9), text))

    outputs = sorted(
        name
        for name, oc in (plan.get("output_changes") or {}).items()
        if tuple((oc or {}).get("actions") or ()) not in (("no-op",), ())
    )
    drift = [
        f"{d.get('address')} ({'/'.join((d.get('change') or {}).get('actions') or [])})"
        for d in plan.get("resource_drift") or []
    ]
    result: dict[str, Any] = {
        "changes": sum(counts.values()) - counts["read"],
        "counts": {k: v for k, v in counts.items() if v},
        "resources": lines[:max_lines],
    }
    if len(lines) > max_lines:
        result["truncated"] = len(lines) - max_lines
    if outputs:
        result["output_changes"] = outputs
    if drift:
        result["drift"] = drift[:20]
    if plan.get("errored"):
        result["errored"] = True
    return result


def plan_file(root: Path, env: str, unit: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", unit.strip("/"))
    return root / PLAN_DIR / env / f"{slug}.tfplan"


def plan_unit(root: Path, env: str, unit: str, *, timeout: float = 900.0) -> dict[str, Any]:
    """Read-only plan of one unit (``-lock=false``), summarised."""
    directory = repo.unit_dir(root, env, unit)
    unit = directory.relative_to(repo.env_dir(root, env)).as_posix()
    out = plan_file(root, env, unit)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    planned = repo.run(
        repo.terragrunt_cmd("plan", "-lock=false", "-input=false", "-no-color", f"-out={out}"),
        cwd=directory,
        timeout=timeout,
        what=f"terragrunt plan ({unit})",
    )
    if planned.returncode != 0:
        raise repo.failure(f"terragrunt plan ({unit})", planned)
    shown = repo.run(
        repo.terragrunt_cmd("show", "-json", "-no-color", str(out)),
        cwd=directory,
        timeout=180.0,
        what=f"terragrunt show ({unit})",
    )
    if shown.returncode != 0:
        raise repo.failure(f"terragrunt show ({unit})", shown)
    try:
        document = json.loads(_json_part(shown.stdout))
    except json.JSONDecodeError as exc:
        raise OpsError(f"terragrunt show -json returned non-JSON output for {unit}: {exc}") from exc
    return {
        "env": env,
        "unit": unit,
        **summarize_plan(document),
        "plan_file": str(out.relative_to(root)),
        "seconds": round(time.monotonic() - started, 1),
    }


def _json_part(text: str) -> str:
    """The JSON document in ``text`` (Terragrunt may print log lines before it)."""
    start = text.find("{")
    return text[start:] if start >= 0 else text
