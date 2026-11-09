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

"""Resolve the catalog for an environment, compile it for Terraform, and describe it.

Merge rules:

* Inside the ``files`` source, an entry (team, caller, target, team profile) or a policy leaf
  defined twice is an error.
* Across sources, a later source may add entries or fill fields an earlier source left unset; a
  different value for a field that is already set is a conflict (error).
* ``environments/<env>.yaml`` ``overrides`` are applied last: maps merge, lists replace, ``null``
  deletes the key.
* ``policy.defaults`` fill ``gateway``, ``user_credential`` and ``observability`` fields that a
  target leaves unset (resolved view only).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from esmeralda.catalog.schema import (
    SCHEMA,
    SCHEMA_VERSION,
    CatalogError,
    check_header,
    load_schema,
    load_yaml,
    validate,
)
from esmeralda.catalog.sources import SECTIONS, Layer, build_sources

CATALOG_DIR = Path("catalog")
SOURCES_FILE = "sources.yaml"

# Targets that run a workload with its own identity, so they can also be callers.
WORKLOAD_KINDS = frozenset({"a2a", "adk"})
# Targets the layer-5 units deploy and the entry point serves (the Terraform `catalog` map).
COMPILED_KINDS = frozenset({"mcp", "a2a", "adk"})
# The serving token each deployed kind requires (Cloud Run IAM vs Agent Runtime).
EXPECTED_TOKEN = {"mcp": "id", "a2a": "access", "adk": "access"}


@dataclass
class Resolved:
    """A resolved catalog: the document (``esmeralda.catalog`` ``resolved`` shape) plus provenance."""

    env: str
    document: dict[str, Any]
    origins: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def teams(self) -> dict[str, Any]:
        return self.document["teams"]

    @property
    def callers(self) -> dict[str, Any]:
        return self.document["callers"]

    @property
    def targets(self) -> dict[str, Any]:
        return self.document["targets"]

    @property
    def policy(self) -> dict[str, Any]:
        return self.document["policy"]


# ------------------------------------------------------------------------------------------
# merging
# ------------------------------------------------------------------------------------------


def _fill(existing: Any, new: Any, path: str, problems: list[str]) -> Any:
    """``new`` merged into ``existing`` without overwriting: unset fields are filled, conflicts reported."""
    if isinstance(existing, dict) and isinstance(new, dict):
        out = dict(existing)
        for key, value in new.items():
            out[key] = _fill(existing[key], value, f"{path}.{key}", problems) if key in existing else value
        return out
    if existing != new:
        problems.append(f"{path}: conflicting values {existing!r} and {new!r}")
    return existing


def _merge_layer(acc: Layer, layer: Layer) -> list[str]:
    problems: list[str] = []
    for section in SECTIONS:
        into = acc.section(section)
        for name, value in layer.section(section).items():
            origin = layer.origins.get((section, name), layer.source)
            if name in into:
                where = f"{section}.{name} ({acc.origins.get((section, name))} vs {origin})"
                into[name] = _fill(into[name], value, where, problems)
            else:
                into[name] = copy.deepcopy(value)
                acc.origins[(section, name)] = origin
    if acc.policy:
        acc.policy = _fill(acc.policy, layer.policy, f"policy (source {layer.source})", problems)
    else:
        acc.policy = copy.deepcopy(layer.policy)
    return problems


def _override(base: Any, patch: Any) -> Any:
    """Environment override: maps merge, lists and scalars replace, ``None`` deletes."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = _override(base[key], value) if key in base else copy.deepcopy(value)
    return out


def _apply_defaults(targets: dict[str, Any], defaults: dict[str, Any]) -> None:
    for target in targets.values():
        if "gateway" in defaults and "gateway" not in target:
            target["gateway"] = defaults["gateway"]
        for key in ("user_credential", "observability"):
            if key in defaults:
                merged = copy.deepcopy(defaults[key])
                merged.update(target.get(key) or {})
                target[key] = merged


# ------------------------------------------------------------------------------------------
# cross-reference checks
# ------------------------------------------------------------------------------------------


def _is_compiled(target: dict[str, Any]) -> bool:
    return not target.get("example") and target["kind"] in COMPILED_KINDS and "unit" in target["deploy"]


def caller_team(resolved_doc: dict[str, Any], caller: str) -> str | None:
    if caller in resolved_doc["callers"]:
        return resolved_doc["callers"][caller]["team"]
    if caller in resolved_doc["targets"]:
        return resolved_doc["targets"][caller]["owner_team"]
    return None


def _check_references(doc: dict[str, Any], origins: dict[tuple[str, str], str]) -> list[str]:
    problems: list[str] = []
    teams, callers, targets, policy = doc["teams"], doc["callers"], doc["targets"], doc["policy"]
    dimensions = set((policy.get("dimensions") or {}).keys())

    def where(section: str, name: str) -> str:
        origin = origins.get((section, name))
        return f"{section[:-1]} '{name}'" + (f" ({origin})" if origin else "")

    for name in sorted(set(callers) & set(targets)):
        problems.append(f"duplicate name '{name}': declared both as a caller ({origins.get(('callers', name))}) "
                        f"and as a target ({origins.get(('targets', name))})")

    for name, profile in doc.get("profiles", {}).items():
        if name not in teams:
            problems.append(f"{where('profiles', name)}: unknown team '{name}' (register it in catalog/teams.yaml)")

    for name, caller in callers.items():
        if caller["team"] not in teams:
            problems.append(f"{where('callers', name)}: unknown team '{caller['team']}'")

    units: dict[str, str] = {}
    for name, target in targets.items():
        label = where("targets", name)
        if target["owner_team"] not in teams:
            problems.append(f"{label}: unknown owner_team '{target['owner_team']}'")

        unit = target["deploy"].get("unit")
        if unit:
            if unit in units:
                problems.append(f"{label}: duplicate deploy.unit '{unit}' (also used by target '{units[unit]}')")
            units[unit] = name
            if unit.rsplit("/", 1)[-1] != name:
                problems.append(f"{label}: deploy.unit '{unit}' must end with the target name (units key the catalog "
                                "by their directory name)")
        if _is_compiled(target):
            serving = target.get("serving") or {}
            for key in ("token", "probe"):
                if key not in serving:
                    problems.append(f"{label}: serving.{key} is required for a deployed {target['kind']} target")
            expected = EXPECTED_TOKEN[target["kind"]]
            if serving.get("token") and serving["token"] != expected:
                problems.append(f"{label}: serving.token must be '{expected}' for kind {target['kind']}")

        for caller, grant in (target.get("callers") or {}).items():
            if caller == name:
                problems.append(f"{label}: lists itself as a caller")
                continue
            if caller in targets:
                if targets[caller]["kind"] not in WORKLOAD_KINDS:
                    problems.append(f"{label}: caller '{caller}' is a {targets[caller]['kind']} target, which has no "
                                    "workload identity to call with")
                    continue
            elif caller not in callers:
                problems.append(f"{label}: unknown caller '{caller}' (declare it under callers, or as an agent target)")
                continue
            team = caller_team(doc, caller)
            allowed = set((teams.get(team) or {}).get("cost_centers") or [])
            for cc in (grant or {}).get("cost_centers") or []:
                if cc not in allowed:
                    problems.append(f"{label}: caller '{caller}' may not bill cost centre '{cc}' (not a cost centre "
                                    f"of its team '{team}')")

        model_targets = {n for n, t in targets.items() if t["kind"] == "model"}
        if target["kind"] != "model":
            for model in target.get("models") or []:
                if model not in model_targets:
                    problems.append(f"{label}: unknown model '{model}' (declare it as a kind: model target)")

        for dim in (target.get("observability") or {}).get("dimensions") or []:
            if dim not in dimensions:
                problems.append(f"{label}: unknown dimension '{dim}' (register it under policy.dimensions)")

    for dim in ((policy.get("defaults") or {}).get("observability") or {}).get("dimensions") or []:
        if dim not in dimensions:
            problems.append(f"policy.defaults.observability: unknown dimension '{dim}'")
    return problems


# ------------------------------------------------------------------------------------------
# resolve
# ------------------------------------------------------------------------------------------


def catalog_dir(root: Path) -> Path:
    path = root / CATALOG_DIR
    if not (path / SOURCES_FILE).is_file():
        raise CatalogError(f"{CATALOG_DIR}/{SOURCES_FILE} not found under {root}")
    return path


def _sources_doc(root: Path, schema: dict[str, Any]) -> dict[str, Any]:
    label = f"{CATALOG_DIR}/{SOURCES_FILE}"
    doc = load_yaml(catalog_dir(root) / SOURCES_FILE, label)
    check_header(doc, label)
    problems = validate(schema, "sources_document", doc, label)
    if problems:
        raise CatalogError(problems)
    return doc


def environments(root: Path) -> list[str]:
    """Environments with a ``catalog/environments/<env>.yaml``."""
    schema = load_schema(root)
    env_dir = catalog_dir(root) / _sources_doc(root, schema).get("environments_dir", "environments")
    return sorted(p.stem for p in env_dir.glob("*.yaml"))


def _environment_doc(root: Path, schema: dict[str, Any], sources_doc: dict[str, Any], env: str) -> dict[str, Any]:
    env_dir = sources_doc.get("environments_dir", "environments")
    path = catalog_dir(root) / env_dir / f"{env}.yaml"
    label = f"{CATALOG_DIR}/{env_dir}/{env}.yaml"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in path.parent.glob("*.yaml"))) or "(none)"
        raise CatalogError(f"unknown environment '{env}': {label} not found (known: {known})")
    doc = load_yaml(path, label)
    check_header(doc, label)
    problems = validate(schema, "environment_document", doc, label)
    if doc.get("environment") != env:
        problems.append(f"{label}: environment is '{doc.get('environment')}', expected '{env}'")
    if problems:
        raise CatalogError(problems)
    return doc


def resolve(root: Path, env: str) -> Resolved:
    """Merge the sources, apply the environment overrides and defaults, and validate."""
    schema = load_schema(root)
    sources_doc = _sources_doc(root, schema)
    env_doc = _environment_doc(root, schema, sources_doc, env)
    sources = build_sources(root, catalog_dir(root), schema, sources_doc)

    acc = Layer(source="resolved")
    problems: list[str] = []
    for source in sources:
        problems += _merge_layer(acc, source.load())
    if problems:
        raise CatalogError(problems)

    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "environment": env,
        "sources": [s.name for s in sources] + [f"environments/{env}.yaml"],
        "teams": acc.teams,
        "profiles": acc.profiles,
        "callers": acc.callers,
        "targets": acc.targets,
        "policy": acc.policy,
    }
    overrides = env_doc.get("overrides") or {}
    for section in ("teams", "callers", "targets", "policy"):
        if overrides.get(section):
            doc[section] = _override(doc[section], overrides[section])
    _apply_defaults(doc["targets"], (doc["policy"].get("defaults") or {}))

    label = f"resolved catalog ({env})"
    problems = validate(schema, "resolved", doc, label)
    if problems:
        raise CatalogError(problems)
    problems = _check_references(doc, acc.origins)
    if problems:
        raise CatalogError(problems)
    return Resolved(env=env, document=doc, origins=acc.origins)


# ------------------------------------------------------------------------------------------
# compile (Terraform) and the committed resolved file
# ------------------------------------------------------------------------------------------


def _compiled_callers(resolved: Resolved, target: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Callers that become IAM members (agents with a unit, service-account callers), and the rest."""
    compiled, other = [], []
    for caller in target.get("callers") or {}:
        if caller in resolved.targets:
            (compiled if _is_compiled(resolved.targets[caller]) else other).append(caller)
        elif resolved.callers[caller]["identity"]["type"] == "service_account":
            compiled.append(caller)
        else:
            other.append(caller)
    return compiled, other


def compile_terraform(resolved: Resolved) -> dict[str, dict[str, Any]]:
    """The ``catalog`` map the layer-5 units read from env.yaml: one entry per deployed target.

    Entry shape (unchanged from the former inline env.yaml map): ``kind``, ``unit``, ``token``,
    ``probe``, optional ``timeout`` and optional ``callers`` (names whose service accounts the
    units grant ``run.invoker`` / ``aiplatform.user``). Example entries, ``managed: false`` targets and
    models are not part of it.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(resolved.targets):
        target = resolved.targets[name]
        if not _is_compiled(target):
            continue
        serving = target["serving"]
        entry: dict[str, Any] = {
            "kind": target["kind"],
            "unit": target["deploy"]["unit"],
            "token": serving["token"],
            "probe": serving["probe"],
        }
        if "timeout" in serving:
            entry["timeout"] = serving["timeout"]
        callers, _ = _compiled_callers(resolved, target)
        if callers:
            entry["callers"] = callers
        out[name] = entry
    return out


def resolved_path(root: Path, env: str) -> Path:
    schema = load_schema(root)
    resolved_dir = _sources_doc(root, schema).get("resolved_dir", ".resolved")
    return catalog_dir(root) / resolved_dir / f"{env}.json"


def render_resolved_file(resolved: Resolved) -> str:
    """Content of ``catalog/.resolved/<env>.json`` (deterministic, committed, read by env.yaml)."""
    doc = {
        "generated_by": f"esmeralda catalog resolve --env {resolved.env} --write",
        "comment": "Generated from catalog/ (see catalog/README.md). Do not edit by hand.",
        "environment": resolved.env,
        "catalog": compile_terraform(resolved),
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def write_resolved(root: Path, env: str) -> Path:
    path = resolved_path(root, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_resolved_file(resolve(root, env)))
    return path


def check_resolved(root: Path, env: str) -> tuple[bool, str]:
    """Whether the committed resolved file matches the sources, and a message saying so."""
    path = resolved_path(root, env)
    expected = render_resolved_file(resolve(root, env))
    rel = path.relative_to(root).as_posix()
    if not path.is_file():
        return False, f"{rel} is missing: run `esmeralda catalog resolve --env {env} --write`"
    if path.read_text() != expected:
        return False, f"{rel} is out of date with catalog/: run `esmeralda catalog resolve --env {env} --write`"
    return True, f"{rel} is up to date"


def _hcl_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_hcl_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_hcl_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"cannot render {type(value).__name__} as HCL")


def render_hcl(resolved: Resolved) -> str:
    """The compiled map as an HCL ``catalog = { ... }`` block (the former inline env.yaml form)."""
    compiled = compile_terraform(resolved)
    width = max((len(n) for n in compiled), default=0)
    lines = ["catalog = {"]
    for name, entry in compiled.items():
        lines.append(f"  {name:<{width}} = {_hcl_value(entry)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------
# show
# ------------------------------------------------------------------------------------------


def show(
    resolved: Resolved,
    *,
    target: str | None = None,
    caller: str | None = None,
    ingress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Who can call each target, through which gateway, and who pays.

    ``ingress`` is env.yaml's ``gateways`` block (default backend + host_rules): the entry-point
    backend of each deployed host.
    """
    if target and target not in resolved.targets:
        raise CatalogError(f"unknown target '{target}' (known: {', '.join(sorted(resolved.targets))})")
    if caller and caller not in resolved.callers and caller not in resolved.targets:
        raise CatalogError(f"unknown caller '{caller}'")
    ingress = ingress or {}
    rows = []
    for name in sorted(resolved.targets):
        if target and name != target:
            continue
        t = resolved.targets[name]
        compiled_callers, _ = _compiled_callers(resolved, t)
        entry_callers = []
        for c, grant in (t.get("callers") or {}).items():
            if caller and c != caller:
                continue
            grant = grant or {}
            team = caller_team(resolved.document, c)
            if c in resolved.targets:
                identity = "workload (agent)"
            else:
                identity = resolved.callers[c]["identity"]["type"]
            entry_callers.append(
                {
                    "caller": c,
                    "identity": identity,
                    "team": team,
                    "gateway": grant.get("gateway") or t.get("gateway"),
                    "pays": {
                        "team": team,
                        "cost_centers": grant.get("cost_centers")
                        or (resolved.teams.get(team) or {}).get("cost_centers", []),
                    },
                    "enforced": c in compiled_callers,
                    **({"tools": grant["tools"]} if grant.get("tools") else {}),
                    **({"quota": grant["quota"]} if grant.get("quota") else {}),
                    **({"product": grant["product"]} if grant.get("product") else {}),
                }
            )
        if caller and not entry_callers:
            continue
        deploy = t["deploy"]
        host_backend = None
        if _is_compiled(t):
            host_backend = (ingress.get("host_rules") or {}).get(name, ingress.get("default"))
        rows.append(
            {
                "target": name,
                "kind": t["kind"],
                "owner_team": t["owner_team"],
                "example": bool(t.get("example")),
                "deploy": deploy.get("unit") or deploy.get("endpoint"),
                "compiled": _is_compiled(t),
                "gateway": t.get("gateway"),
                "ingress_backend": host_backend,
                "callers": entry_callers,
            }
        )
    return {
        "env": resolved.env,
        "policy": resolved.policy.get("attribution", {}),
        "targets": rows,
    }
