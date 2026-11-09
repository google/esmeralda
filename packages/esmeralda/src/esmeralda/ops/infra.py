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

_LAYER_RE = re.compile(r"(?:^|/)layer-(\d+)-")
_DEP_BLOCK_RE = re.compile(r'dependency\s+"([^"]+)"\s*\{(.*?)\n\}', re.DOTALL)
_CONFIG_PATH_RE = re.compile(r'config_path\s*=\s*"([^"]+)"')
_EXCLUDE_GW_RE = re.compile(r'exclude\s*\{[^}]*gateways\.enabled\s*,\s*"([^"]+)"', re.DOTALL)
_PLAN_TEXT_SUMMARY_RE = re.compile(
    r"Plan:\s*(\d+)\s+to\s+add,\s*(\d+)\s+to\s+change,\s*(\d+)\s+to\s+destroy"
)


def unit_layer(unit: str) -> int | None:
    match = _LAYER_RE.search(unit)
    return int(match.group(1)) if match else None


def resolve_unit(root: Path, env: str, unit: str) -> str:
    """Resolves ``unit`` (exact path or unambiguous short name like ``entrypoint``) in ``env``."""
    cleaned = unit.strip().strip("/")
    known = repo.units(root, env)
    if cleaned in known:
        return cleaned
    if cleaned in repo._MOVED_UNITS and repo._MOVED_UNITS[cleaned] in known:
        return repo._MOVED_UNITS[cleaned]
    exact_tail = [u for u in known if u.rsplit("/", 1)[-1] == cleaned or u.endswith(f"/{cleaned}")]
    if len(exact_tail) == 1:
        return exact_tail[0]
    repo.unit_dir(root, env, cleaned)
    return cleaned


def unit_dependencies(root: Path, env: str, unit: str) -> list[str]:
    """Dependency unit paths declared in ``terragrunt.hcl`` for ``unit``."""
    base = repo.env_dir(root, env)
    hcl_path = base / unit / repo.UNIT_FILE
    if not hcl_path.is_file():
        return []
    text = hcl_path.read_text(errors="replace")
    deps: list[str] = []
    for match in _DEP_BLOCK_RE.finditer(text):
        dep_name, body = match.group(1), match.group(2)
        cp_match = _CONFIG_PATH_RE.search(body)
        if cp_match:
            raw_path = cp_match.group(1)
            resolved = (hcl_path.parent / raw_path).resolve()
            try:
                dep_unit = resolved.relative_to(base.resolve()).as_posix()
            except ValueError:
                try:
                    dep_unit = resolved.relative_to((root / repo.LIVE).resolve()).as_posix()
                except ValueError:
                    dep_unit = raw_path
            if dep_unit not in deps:
                deps.append(dep_unit)
        elif dep_name not in deps:
            deps.append(dep_name)
    return deps


def unit_enabled(root: Path, env: str, unit: str) -> bool:
    """Whether ``unit`` is enabled in ``env.yaml`` (checks ``exclude`` blocks and ``gateways``)."""
    hcl_path = repo.env_dir(root, env) / unit / repo.UNIT_FILE
    gw = repo.gateways_block(root, env)
    enabled_gws = gw.get("enabled")
    if unit == "layer-5-workloads/gateways/direct":
        host_rules = gw.get("host_rules") or {}
        if gw.get("default") or host_rules:
            return gw.get("default") == "direct" or "direct" in host_rules.values()
        return True
    if hcl_path.is_file():
        text = hcl_path.read_text(errors="replace")
        match = _EXCLUDE_GW_RE.search(text)
        if match and isinstance(enabled_gws, list):
            return match.group(1) in enabled_gws
    if unit.startswith("layer-5-workloads/gateways/") and isinstance(enabled_gws, list) and enabled_gws:
        gw_name = unit.rsplit("/", 1)[-1]
        return gw_name in enabled_gws
    return True


def list_units(root: Path, env: str, *, layer: int | None = None) -> dict[str, Any]:
    states = repo.all_states(root, env)
    items: list[dict[str, Any]] = []
    if layer == 0:
        cicd_hcl = root / repo.LIVE / "shared" / "layer-0-cicd" / repo.UNIT_FILE
        if cicd_hcl.is_file():
            cicd_state = root / repo.LIVE / ".local_states" / "shared" / "layer-0-cicd" / "terraform.tfstate"
            items.append(
                {
                    "unit": "shared/layer-0-cicd",
                    "layer": 0,
                    "deployed": cicd_state.is_file(),
                    "local_state": cicd_state.is_file(),
                    "resources": None,
                    "enabled": True,
                    "excluded": False,
                    "dependencies": [],
                }
            )
    for s in states:
        ulayer = unit_layer(s.unit)
        if layer is not None and ulayer != layer:
            continue
        enabled = unit_enabled(root, env, s.unit)
        items.append(
            {
                "unit": s.unit,
                "layer": ulayer,
                "deployed": s.deployed,
                "local_state": repo.state_path(root, env, s.unit).is_file(),
                "resources": s.resources,
                "enabled": enabled,
                "excluded": not enabled,
                "dependencies": unit_dependencies(root, env, s.unit),
            }
        )
    return {
        "env": env,
        "backend": repo.backend(root, env),
        "layer": layer,
        "units": items,
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
    # Prefer the flattened layer-5-workloads/test-vm unit; fall back to services/test-vm for one release.
    vm = by_unit.get("layer-5-workloads/test-vm") or by_unit.get("layer-5-workloads/services/test-vm")
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
        resolved = resolve_unit(root, env, unit)
        state = repo.unit_state(root, env, resolved)
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


def plan_unit(
    root: Path,
    env: str,
    unit: str = "--all",
    *,
    all_units: bool = False,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Read-only plan of one unit (``-lock=false``) or ``--all`` enabled units, summarised."""
    if all_units or unit == "--all":
        return plan_all(root, env, timeout=timeout)
    resolved = resolve_unit(root, env, unit)
    directory = repo.unit_dir(root, env, resolved)
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
    summary = summarize_plan(document)
    return {
        "env": env,
        "unit": unit,
        **summary,
        "plan_file": str(out.relative_to(root)),
        "seconds": round(time.monotonic() - started, 1),
    }


def plan_all(root: Path, env: str, *, timeout: float = 900.0) -> dict[str, Any]:
    """Plans every enabled unit in ``env`` and aggregates the counts."""
    started = time.monotonic()
    enabled_units = [u for u in repo.units(root, env) if unit_enabled(root, env, u)]
    results: list[dict[str, Any]] = []
    totals = {"add": 0, "change": 0, "destroy": 0, "replace": 0, "read": 0}
    total_changes = 0
    for u in enabled_units:
        res = plan_unit(root, env, u, timeout=timeout)
        results.append(res)
        total_changes += int(res.get("changes", 0))
        for k, v in (res.get("counts") or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return {
        "env": env,
        "unit": "--all",
        "all": True,
        "changes": total_changes,
        "counts": {k: v for k, v in totals.items() if v},
        "units": results,
        "seconds": round(time.monotonic() - started, 1),
    }


def apply_unit(
    root: Path,
    env: str,
    unit: str,
    *,
    allow_destroy: bool = False,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Plans ``unit`` to a planfile first, enforces the destroy guard, and applies the saved planfile."""
    resolved = resolve_unit(root, env, unit)
    directory = repo.unit_dir(root, env, resolved)
    planned = plan_unit(root, env, resolved, timeout=timeout)
    counts = planned.get("counts") or {}
    destroy_count = int(counts.get("destroy", 0)) + int(counts.get("replace", 0))
    if destroy_count > 0 and not allow_destroy:
        destructive = [
            r for r in (planned.get("resources") or []) if r.startswith(("-", "-/+", "+/-"))
        ]
        return {
            **planned,
            "applied": False,
            "refused": True,
            "exit_code": 2,
            "destroy_count": destroy_count,
            "destructive_resources": destructive,
            "reason": (
                f"Refusing to apply {env}/{resolved}: plan has {destroy_count} destroy/replace action(s). "
                "Pass --allow-destroy (or ALLOW_DESTROY=1) to permit destructive changes."
            ),
        }

    out = root / planned["plan_file"]
    started = time.monotonic()
    applied = repo.run(
        repo.terragrunt_cmd("apply", "-input=false", "-no-color", str(out)),
        cwd=directory,
        timeout=timeout,
        what=f"terragrunt apply ({resolved})",
    )
    if applied.returncode != 0:
        raise repo.failure(f"terragrunt apply ({resolved})", applied)
    out.unlink(missing_ok=True)
    return {
        **planned,
        "applied": True,
        "refused": False,
        "exit_code": 0,
        "apply_seconds": round(time.monotonic() - started, 1),
    }


def _json_part(text: str) -> str:
    """The JSON document in ``text`` (Terragrunt may print log lines before it)."""
    start = text.find("{")
    return text[start:] if start >= 0 else text
