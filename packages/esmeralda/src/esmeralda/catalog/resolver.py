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
import re
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
# Agent Registry entries that get a per-target Agent Gateway policy, by IAP_TARGET_POLICIES_JSON group.
IAP_GROUPS = {"mcp": "mcp_servers", "a2a": "agents"}
# `managed: false` targets compiled into Agent Registry entries: kind -> (entry type, protocol binding).
EXTERNAL_REGISTRY = {
    "http": ("endpoint", "HTTP_JSON"),
    "model": ("endpoint", "HTTP_JSON"),
    "mcp": ("mcp_server", "JSONRPC"),
    "a2a": ("agent", "HTTP+JSON"),
}
# IAP_TARGET_POLICIES_JSON group of each Agent Registry entry type.
REGISTRY_GROUPS = {"endpoint": "endpoints", "mcp_server": "mcp_servers", "agent": "agents"}
# Every group of the compiled iap_egress lists, always present (possibly empty).
IAP_EGRESS_GROUPS = ("agents", "endpoints", "mcp_servers")
# Placeholders an external endpoint may use instead of environment-specific literals; the registering
# unit substitutes them from layer outputs ({domain}: layer 2 entrypoint_domain, {region}: env.yaml).
ENDPOINT_PLACEHOLDERS = frozenset({"domain", "region"})
# Hosts layer 4 registers for every agent (system endpoints): never a per-target catalog entry.
SYSTEM_HOST_SUFFIX = "googleapis.com"
# Agent unit output holding the agent's Agent Identity principal (principal://...).
WORKLOAD_PRINCIPAL_OUTPUT = "agent_identity_principal"
# Units whose service-account outputs the iap-egress unit maps (identity_refs); agent principals aside.
IAP_REF_SOURCES = frozenset({"layer-3-security"})
# Environments where policy.attribution.unmapped_caller may be `flag` unless the policy lists others.
DEFAULT_FLAG_ENVIRONMENTS = ("dev",)
# Targets that serve requests in a process the caller guard can run in (Cloud Run MCP, A2A servers).
GUARDED_KINDS = frozenset({"mcp", "a2a"})
# principal_teams.principal_type per catalog identity type (the governance table's principal_type values).
PRINCIPAL_TYPE = {
    "service_account": "service_account",
    "spiffe": "spiffe",
    "group": "group",
    "apigee_app": "apigee_app",
    "federated": "oidc",
}
# principal_type of an agent's Agent Identity principal (principal://.../reasoningEngines/<id>).
AGENT_PRINCIPAL_TYPE = "agent_runtime"


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


_ENDPOINT = re.compile(r"^https://(?P<host>[^/:?#]+)(?::(?P<port>[0-9]{1,5}))?(?P<path>/[^?#]*)?$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def endpoint_host(endpoint: str) -> str:
    """Host of an external endpoint, placeholders kept (``apigee.{domain}``); empty if malformed."""
    match = _ENDPOINT.match(endpoint)
    return match["host"] if match else ""


def _is_system_endpoint(target: dict[str, Any]) -> bool:
    """A ``managed: false`` target on a Google API host: layer 4 registers it for every agent."""
    host = endpoint_host(target["deploy"].get("endpoint", ""))
    return host == SYSTEM_HOST_SUFFIX or host.endswith("." + SYSTEM_HOST_SUFFIX)


def _is_external(target: dict[str, Any]) -> bool:
    """A non-example ``managed: false`` target that gets its own Agent Registry entry and caller list."""
    return (
        not target.get("example")
        and target["deploy"].get("managed") is False
        and target["kind"] in EXTERNAL_REGISTRY
        and not _is_system_endpoint(target)
    )


def _registry_group(target: dict[str, Any]) -> str | None:
    """The iap_egress group of a target with its own Agent Registry entry (``None``: no entry)."""
    if _is_compiled(target):
        return IAP_GROUPS.get(target["kind"])
    if _is_external(target):
        return REGISTRY_GROUPS[EXTERNAL_REGISTRY[target["kind"]][0]]
    return None


def _external_problems(label: str, name: str, target: dict[str, Any]) -> list[str]:
    """Endpoint shape of a non-example ``managed: false`` target (placeholders, ``{domain}`` hosts)."""
    endpoint = target["deploy"]["endpoint"]
    if target["kind"] == "adk":
        return [f"{label}: kind adk means an agent deployed on Agent Runtime (deploy.unit); declare an agent "
                "running elsewhere as kind a2a with managed: false"]
    problems = []
    unknown = sorted({p for p in _PLACEHOLDER.findall(endpoint) if p not in ENDPOINT_PLACEHOLDERS})
    if unknown:
        problems.append(f"{label}: unknown endpoint placeholder(s) {', '.join('{' + p + '}' for p in unknown)} "
                        f"(known: {', '.join('{' + p + '}' for p in sorted(ENDPOINT_PLACEHOLDERS))})")
    host = endpoint_host(endpoint)
    if not host:
        problems.append(f"{label}: deploy.endpoint '{endpoint}' is not https://<host>[:port][/path]")
        return problems
    if not re.fullmatch(r"[a-z0-9.{}-]+", host):
        problems.append(f"{label}: deploy.endpoint host '{host}' must be an exact hostname (letters, digits, dots, "
                        "hyphens, placeholders): the Agent Registry matches exact hostnames, no wildcards")
    if "{domain}" in host and not re.fullmatch(r"[a-z0-9-]+\.\{domain\}", host):
        problems.append(f"{label}: deploy.endpoint host '{host}' must be one label under {{domain}} "
                        "(<label>.{domain}: the entry point certificate covers *.{domain} only)")
    return problems


def caller_team(resolved_doc: dict[str, Any], caller: str) -> str | None:
    if caller in resolved_doc["callers"]:
        return resolved_doc["callers"][caller]["team"]
    if caller in resolved_doc["targets"]:
        return resolved_doc["targets"][caller]["owner_team"]
    return None


def _iap_ref_problems(label: str, caller: str, doc: dict[str, Any]) -> list[str]:
    """A gateway-listed caller's service-account ref must come from a unit the iap-egress unit reads."""
    if caller in doc["callers"]:
        entry = doc["callers"][caller]
        if entry.get("example") or entry.get("agent_gateway") is False:
            return []
        identity = entry["identity"]
    elif caller in doc["targets"]:
        identity = doc["targets"][caller].get("identity") or {}
    else:
        return []
    ref = identity.get("ref") if identity.get("type") == "service_account" else None
    if ref and ref.split(":", 1)[0] not in IAP_REF_SOURCES:
        return [f"{label}: caller '{caller}' would go on the Agent Gateway list as '{ref}', but the iap-egress "
                f"unit only maps {', '.join(sorted(IAP_REF_SOURCES))} refs (and agent principals); set "
                "'agent_gateway: false' on a caller that doesn't call through the Agent Gateway"]
    return []


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
            if _registry_group(target):
                problems += _iap_ref_problems(label, caller, doc)
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
    return problems + _check_external_hosts(doc, where)


def _check_external_hosts(doc: dict[str, Any], where: Any) -> list[str]:
    """External endpoints: valid shape, one target per host, no clash with an entry point host."""
    problems: list[str] = []
    targets = doc["targets"]
    served = {f"{name}.{{domain}}": name for name, t in targets.items() if _is_compiled(t)}
    hosts: dict[str, str] = {}
    for name in sorted(targets):
        target = targets[name]
        if target.get("example") or target["deploy"].get("managed") is not False:
            continue
        label = where("targets", name)
        found = _external_problems(label, name, target)
        problems += found
        if found or not _is_external(target):
            continue
        host = endpoint_host(target["deploy"]["endpoint"])
        if host in hosts:
            problems.append(f"{label}: endpoint host '{host}' is also the host of target '{hosts[host]}' (one "
                            "Agent Registry entry, so one caller list, per host)")
        hosts.setdefault(host, name)
        if host in served:
            problems.append(f"{label}: endpoint host '{host}' is the entry point host of deployed target "
                            f"'{served[host]}'")
    return problems


def attribution_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """``policy.attribution`` with its defaults: fail closed (deny / reject), flag only in dev."""
    attribution = policy.get("attribution") or {}
    return {
        "unmapped_caller": attribution.get("unmapped_caller", "deny"),
        "declared_mismatch": attribution.get("declared_mismatch", "reject"),
        "flag_environments": list(attribution.get("flag_environments", DEFAULT_FLAG_ENVIRONMENTS)),
    }


def _check_attribution(doc: dict[str, Any], env: str) -> list[str]:
    attribution = attribution_policy(doc["policy"])
    if attribution["unmapped_caller"] == "flag" and env not in attribution["flag_environments"]:
        allowed = ", ".join(attribution["flag_environments"]) or "(none)"
        return [f"policy.attribution.unmapped_caller: 'flag' is not allowed in environment '{env}' (only in: "
                f"{allowed}); unknown callers must be denied there"]
    return []


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
    problems = _check_references(doc, acc.origins) + _check_attribution(doc, env)
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


def _identity_member(identity: dict[str, Any] | None) -> str | None:
    """A service-account identity as an IAP member: its ``<unit>:<output>`` ref, or its literal id."""
    if not identity or identity.get("type") != "service_account":
        return None
    return identity.get("ref") or identity.get("id")


def compile_iap_egress(resolved: Resolved) -> dict[str, dict[str, list[str]]]:
    """The Agent Gateway per-target ``roles/iap.egressor`` member lists, derived from target callers.

    Shape: ``{"agents": {...}, "endpoints": {...}, "mcp_servers": {...}}`` (the
    ``IAP_TARGET_POLICIES_JSON`` layout of ``grant_iap_egress.sh``): deployed ``a2a`` / ``mcp``
    targets, and non-example ``managed: false`` targets by their Agent Registry entry type (``http``
    and non-Google ``model`` endpoints under ``endpoints``, ``mcp`` under ``mcp_servers``, ``a2a``
    under ``agents``). Every such target is listed, with an empty list when no caller is enforced:
    the script then writes an empty policy (nobody), never skips the entry. Members are unresolved
    references the ``iap-egress`` unit maps to unit outputs: ``<unit>:agent_identity_principal``
    (an agent caller's Agent Identity principal) and ``<unit>:<output>`` service-account refs
    (``layer-3-security:<x>_sa_email``); a literal service-account id passes through unchanged.
    Per target, in order: each agent caller's principal then service account, the policy
    ``agent_gateway.shared_workload_identity`` when the target has an agent caller (``mcp`` / ``a2a``
    kinds it lists, never endpoints), then the other service-account callers. Callers that are not
    enforced (groups, examples, ...) are skipped, the same set ``compile_terraform`` leaves out of
    ``callers``, and so are callers marked ``agent_gateway: false``: they reach the target directly
    (an Apigee proxy runtime, say), so they keep the target's IAM grant but never hold
    ``roles/iap.egressor`` on its registry entry.
    """
    shared = (resolved.policy.get("agent_gateway") or {}).get("shared_workload_identity") or {}
    out: dict[str, dict[str, list[str]]] = {group: {} for group in IAP_EGRESS_GROUPS}
    for name in sorted(resolved.targets):
        target = resolved.targets[name]
        group = _registry_group(target)
        if not group:
            continue
        callers, _ = _compiled_callers(resolved, target)
        workload: list[str] = []
        others: list[str] = []
        for caller in callers:
            if caller in resolved.targets:
                agent = resolved.targets[caller]
                workload.append(f"{agent['deploy']['unit']}:{WORKLOAD_PRINCIPAL_OUTPUT}")
                member = _identity_member(agent.get("identity"))
                if member:
                    workload.append(member)
            else:
                if resolved.callers[caller].get("agent_gateway") is False:
                    continue  # reaches the target directly (e.g. Apigee): IAM only, no gateway grant
                member = _identity_member(resolved.callers[caller]["identity"])
                if member:
                    others.append(member)
        members = list(workload)
        if workload and shared and group != "endpoints" and target["kind"] in shared.get("kinds", []):
            members.append(shared["ref"])
        members += others
        out[group][name] = list(dict.fromkeys(members))
    return out


def compile_external_targets(resolved: Resolved) -> dict[str, dict[str, Any]]:
    """The Agent Registry entries of non-example ``managed: false`` targets (``external_targets``).

    One entry per target, keyed by target name (the entry's ``service_id``): ``kind``, ``registry``
    (``endpoint`` | ``mcp_server`` | ``agent``), ``url`` (the catalog endpoint, placeholders such as
    ``{domain}`` kept for the ``iap-egress`` unit to substitute from layer outputs),
    ``protocol_binding``, ``description`` and ``owner_team``. Targets on Google API hosts are left
    out: layer 4 registers those for every agent (system endpoints). Their caller lists are in
    ``iap_egress`` under the matching group.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(resolved.targets):
        target = resolved.targets[name]
        if not _is_external(target):
            continue
        registry, binding = EXTERNAL_REGISTRY[target["kind"]]
        out[name] = {
            "kind": target["kind"],
            "registry": registry,
            "url": target["deploy"]["endpoint"],
            "protocol_binding": binding,
            "description": target.get("description") or name,
            "owner_team": target["owner_team"],
        }
    return out


# ------------------------------------------------------------------------------------------
# verified attribution (principal -> caller -> team)
# ------------------------------------------------------------------------------------------


def _principal(identity: dict[str, Any] | None) -> tuple[str, str] | None:
    """``(principal, principal_type)`` of a catalog identity, in the form ``caller.principal`` takes.

    Service accounts and SPIFFE identities stay unresolved references (``<unit>:<output>``) when
    they have one; the unit that writes the table maps them to values. Groups read
    ``group:<email>``, federated identities ``<issuer>#<subject>`` (two issuers may share a subject).
    """
    if not identity:
        return None
    kind = identity.get("type")
    if kind in ("service_account", "spiffe"):
        value = identity.get("ref") or identity.get("id")
    elif kind == "group":
        value = f"group:{identity['id']}"
    elif kind == "apigee_app":
        value = identity["id"]
    elif kind == "federated":
        value = f"{identity['issuer']}#{identity['subject']}"
    else:
        return None
    return (value, PRINCIPAL_TYPE[kind]) if value else None


def _workload_principals(target: dict[str, Any]) -> list[tuple[str, str]]:
    """An agent's verified identities: its Agent Identity principal, then the identity it calls out as."""
    out = [(f"{target['deploy']['unit']}:{WORKLOAD_PRINCIPAL_OUTPUT}", AGENT_PRINCIPAL_TYPE)]
    own = _principal(target.get("identity"))
    if own:
        out.append(own)
    return out


def _shared_identity(resolved: Resolved) -> dict[str, Any]:
    return (resolved.policy.get("agent_gateway") or {}).get("shared_workload_identity") or {}


def _referenced_callers(resolved: Resolved) -> set[str]:
    """Callers some target of this environment lists (examples aside)."""
    return {
        caller
        for target in resolved.targets.values()
        if not target.get("example")
        for caller in (target.get("callers") or {})
    }


def compile_principal_teams(resolved: Resolved) -> list[dict[str, Any]]:
    """Rows of the ``principal_teams`` attribution table: every verified principal of the environment.

    One row per principal: ``principal`` (a ``<unit>:<output>`` reference, a literal id,
    ``group:<email>`` or ``<issuer>#<subject>``), ``principal_type`` (``agent_runtime``,
    ``service_account``, ``spiffe``, ``group``, ``apigee_app``, ``oidc``), ``caller`` (catalog caller
    or agent target), ``team`` and the team's ``cost_centers``. Deployed agents contribute their
    Agent Identity principal and the service account they call out as. Callers contribute their
    identity when a target of the environment lists them, or when the identity is not a unit output
    (groups, literal ids): a unit-output identity no target uses may not exist in the environment.
    Example entries are left out, and so is the policy's shared workload identity: several agents
    present it, so it can't name one team (the fix is per-agent outbound identity, #134). A
    principal mapped to two different teams is an error.
    """
    shared = _shared_identity(resolved).get("ref")
    referenced = _referenced_callers(resolved)
    rows: dict[str, dict[str, Any]] = {}
    problems: list[str] = []

    def add(principal: str, principal_type: str, caller: str, team: str) -> None:
        if principal == shared:
            return
        row = {
            "principal": principal,
            "principal_type": principal_type,
            "caller": caller,
            "team": team,
            "cost_centers": list((resolved.teams.get(team) or {}).get("cost_centers") or []),
        }
        existing = rows.get(principal)
        if existing and existing["team"] != team:
            problems.append(f"principal '{principal}' maps to team '{existing['team']}' (caller "
                            f"'{existing['caller']}') and to team '{team}' (caller '{caller}')")
        rows.setdefault(principal, row)

    for name in sorted(resolved.targets):
        target = resolved.targets[name]
        if target["kind"] in WORKLOAD_KINDS and _is_compiled(target):
            for principal, principal_type in _workload_principals(target):
                add(principal, principal_type, name, target["owner_team"])
    for name in sorted(resolved.callers):
        entry = resolved.callers[name]
        if entry.get("example"):
            continue
        if entry["identity"].get("ref") and name not in referenced:
            continue
        found = _principal(entry["identity"])
        if found:
            add(found[0], found[1], name, entry["team"])
    if problems:
        raise CatalogError(problems)
    return [rows[p] for p in sorted(rows)]


def compile_caller_allowlists(resolved: Resolved) -> dict[str, list[dict[str, Any]]]:
    """Per guarded target (deployed ``mcp`` / ``a2a``), the principals the in-process guard accepts.

    Entries: ``principal``, ``caller``, ``team`` and ``cost_centers`` (the cost centres the caller
    may declare on this target: its grant's, else its team's). The callers are the same set that
    gets the target's IAM grant (agents with a unit, service-account callers, including those
    marked ``agent_gateway: false``). When agents call out as the policy's shared workload identity
    (targets of ``shared_workload_identity.kinds`` with an agent caller), that identity is accepted
    too, flagged ``shared``: its team is the agents' team when they all belong to one team, else
    ``null`` (the call is verified but the gateway record decides who pays).
    """
    shared = _shared_identity(resolved)
    out: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(resolved.targets):
        target = resolved.targets[name]
        if not _is_compiled(target) or target["kind"] not in GUARDED_KINDS:
            continue
        compiled, _ = _compiled_callers(resolved, target)
        grants = target.get("callers") or {}
        entries: list[dict[str, Any]] = []
        agents: list[tuple[str, str, list[str]]] = []
        for caller in compiled:
            team = caller_team(resolved.document, caller)
            cost_centers = list((grants.get(caller) or {}).get("cost_centers")
                                or (resolved.teams.get(team) or {}).get("cost_centers") or [])
            if caller in resolved.targets:
                principals = _workload_principals(resolved.targets[caller])
                agents.append((caller, team, cost_centers))
            else:
                found = _principal(resolved.callers[caller]["identity"])
                principals = [found] if found else []
            for principal, _ in principals:
                entries.append({"principal": principal, "caller": caller, "team": team, "cost_centers": cost_centers})
        if agents and shared and target["kind"] in shared.get("kinds", []):
            teams = {team for _, team, _ in agents}
            single = next(iter(teams)) if len(teams) == 1 else None
            entries.append({
                "principal": shared["ref"],
                "caller": "+".join(caller for caller, _, _ in agents),
                "team": single,
                "cost_centers": sorted({cc for _, _, ccs in agents for cc in ccs}) if single else [],
                "shared": True,
            })
        seen: set[str] = set()
        out[name] = [e for e in entries if not (e["principal"] in seen or seen.add(e["principal"]))]
    return out


def compile_attribution(resolved: Resolved) -> dict[str, Any]:
    """The ``attribution`` block of ``catalog/.resolved/<env>.json``.

    ``policy`` (``unmapped_caller``, ``declared_mismatch``), ``principal_teams`` (the table rows),
    ``shared_principals`` and ``allowlists`` (per guarded target). Also checks that every identity
    the catalog lets through a gateway or IAM grant can be attributed: allowed implies attributable.
    """
    policy = attribution_policy(resolved.policy)
    rows = compile_principal_teams(resolved)
    shared = _shared_identity(resolved).get("ref")
    known = {row["principal"] for row in rows} | ({shared} if shared else set())
    known_callers = {row["caller"] for row in rows}
    problems: list[str] = []
    for group, targets in compile_iap_egress(resolved).items():
        for target, members in targets.items():
            for member in members:
                if member not in known:
                    problems.append(f"iap_egress.{group}.{target}: '{member}' is allowed through the Agent Gateway "
                                    "but has no principal_teams row (it couldn't be attributed)")
    for target, entry in compile_terraform(resolved).items():
        for caller in entry.get("callers", []):
            if caller not in known_callers:
                problems.append(f"catalog.{target}: caller '{caller}' gets an IAM grant but has no principal_teams row")
    if problems:
        raise CatalogError(problems)
    return {
        "policy": {k: policy[k] for k in ("unmapped_caller", "declared_mismatch")},
        "principal_teams": rows,
        "shared_principals": [shared] if shared else [],
        "allowlists": compile_caller_allowlists(resolved),
    }


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
        "external_targets": compile_external_targets(resolved),
        "iap_egress": compile_iap_egress(resolved),
        "attribution": compile_attribution(resolved),
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


def _registry_entry(target: dict[str, Any]) -> str | None:
    """Where the Agent Gateway finds this target, for ``show``."""
    group = _registry_group(target)
    if group:
        return f"{group} (per-target caller list)"
    if not target.get("example") and target["deploy"].get("managed") is False and _is_system_endpoint(target):
        return "system endpoint (layer 4, every agent; callers not enforced at the gateway)"
    return None


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
                    "enforced": c in compiled_callers and (_is_compiled(t) or _is_external(t)),
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
                "registry": _registry_entry(t),
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
