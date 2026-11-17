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

"""``agent_registry``: discovered inventory (agents, MCP servers, endpoints) from Agent Registry.

Inventory only, never authorization. ``esmeralda catalog fetch --source <name>`` lists the
``agents``, ``mcpServers`` and ``endpoints`` of the Agent Registry of the configured projects
(read-only ``gcloud alpha agent-registry ... list``), including what the runtimes registered
automatically (Agent Runtime engines, Cloud Run services, GKE workloads), and writes one snapshot
entry per resource: ``{id, name, inventory}`` with the registry id, a deterministic catalog name,
the kind, description, first ``https`` interface, interfaces, A2A skills and runtime resource.
Project ids and numbers, the region and the entry point domain become placeholders; runtime
identities (principals) are dropped.

**No loop.** What Esmeralda registers itself is never imported: entries backed by an Agent
Registry service of the compiled catalog (``catalog/.resolved/<env>.json``: deployed targets,
``external_targets``, ``google_apis``; plus the runtime's ``core-gapi-services*``) and the Agent
Runtime engines of the deployed agents (their units' ``engine_id``) are excluded at fetch time.

At resolve time every entry is a **discovered** inventory entry: shown by ``catalog show
--inventory``, reported by ``catalog diff``, never compiled (no IAM, no Agent Gateway policy, no
registry write, no allowlist). A catalog file adopts one only through an explicit pin,
``deploy.registry: {source: <name>, id: <registry id>}``; an entry whose name equals an unpinned
catalog target fails the resolve, and so does a pin whose id the snapshot doesn't hold.

Options (``sources.yaml``)::

    - name: agent-registry
      type: agent_registry
      enabled: false
      options:
        projects: [governance, ai_coe_agents, cx_agents, mcps]   # aliases of layer-1 <alias>_project_id
        location: us-central1           # default: the environment's region
        names: {"<registry id>": loan-helper}   # catalog name for an entry (default: derived)
        exclude: ["urn:agent:googleapis.com:*"]  # registry id patterns to skip (default: Google's global agents)
        model_hosts: []                 # endpoint hosts reported as kind model instead of http
"""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

from esmeralda.catalog.adapters import _live
from esmeralda.catalog.schema import CatalogError
from esmeralda.catalog.snapshot import Placeholders, SnapshotSource, SourceUnreachable, normalize_placeholders
from esmeralda.catalog.sources import register_source

PROJECTS_UNIT = "layer-1-projects"
NETWORK_UNIT = "layer-2-networking"
AGENT_UNIT = "layer-5-workloads/{unit}"
PROJECT_ID_SUFFIX = "_project_id"
DEFAULT_PROJECTS = ("governance",)
DEFAULT_EXCLUDE = ("urn:agent:googleapis.com:*",)
# Registry collection (gcloud group) -> (catalog kind, id field of the resource).
COLLECTIONS = {
    "agents": ("a2a", "agentId"),
    "mcp-servers": ("mcp", "mcpServerId"),
    "endpoints": ("http", "endpointId"),
}
RUNTIME_REFERENCE = "agentregistry.googleapis.com/system/RuntimeReference"
# Runtime resource prefixes -> catalog runtime.
RUNTIMES = {
    "//aiplatform.googleapis.com/": "agent_runtime",
    "//run.googleapis.com/": "cloud_run",
    "//container.googleapis.com/": "gke",
}
# Agent Registry services the runtimes create for themselves (Google APIs every engine reaches).
RUNTIME_SERVICE_PREFIXES = ("core-gapi-services",)
_SERVICE_REF = re.compile(r"^//agentregistry\.googleapis\.com/projects/[^/]+/locations/[^/]+/services/(?P<sid>[^/]+)$")
_ENGINE = re.compile(r"reasoningEngines/(?P<id>[^/]+)$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


def derive_name(raw: str) -> str:
    """A catalog-name candidate from a service id or display name (lower case, ``-`` separators)."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower())).strip("-")


def runtime_of(reference: str) -> str | None:
    return next((runtime for prefix, runtime in RUNTIMES.items() if reference.startswith(prefix)), None)


def _interfaces(item: dict[str, Any]) -> list[dict[str, str]]:
    """Interfaces of a registry resource (agents: their protocols and A2A card), https only."""
    found: list[dict[str, Any]] = list(item.get("interfaces") or [])
    for protocol in item.get("protocols") or []:
        found += protocol.get("interfaces") or []
    card = (item.get("card") or {}).get("content") or {}
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except json.JSONDecodeError:
            card = {}
    found += card.get("supportedInterfaces") or card.get("supported_interfaces") or []
    if card.get("url"):
        found.append({"url": card["url"], "protocolBinding": card.get("preferredTransport", "")})
    out: dict[str, dict[str, str]] = {}
    for interface in found:
        url = interface.get("url") if isinstance(interface, dict) else None
        if isinstance(url, str) and url.startswith("https://") and url not in out:
            binding = interface.get("protocolBinding") or interface.get("protocol_binding") or ""
            out[url] = {"url": url, **({"protocol_binding": binding} if binding else {})}
    return [out[url] for url in sorted(out)]


def _skills(item: dict[str, Any]) -> list[str]:
    card = (item.get("card") or {}).get("content") or {}
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except json.JSONDecodeError:
            card = {}
    skills = list(item.get("skills") or []) + list(card.get("skills") or [])
    return sorted({s.get("id") or s.get("name") for s in skills
                   if isinstance(s, dict) and (s.get("id") or s.get("name"))})


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


@register_source("agent_registry")
class AgentRegistrySource(SnapshotSource):
    """Discovered inventory from Agent Registry (see the module docstring)."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._placeholders = Placeholders()
        self._expected: set[str] = set()  # fetch only: service ids the compiled catalog registers
        self._registered: set[str] = set()  # fetch only: service ids the live registry lists

    # -- options --------------------------------------------------------------------------

    def _strings(self, option: str, default: tuple[str, ...]) -> list[str]:
        value = self.options.get(option, list(default))
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.{option} must be a list of strings")
        return value

    @property
    def project_aliases(self) -> list[str]:
        aliases = self._strings("projects", DEFAULT_PROJECTS)
        bad = [a for a in aliases if not _ALIAS.match(a)]
        if bad or len(set(aliases)) != len(aliases):
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.projects must list unique layer-1 project "
                               f"aliases matching {_ALIAS.pattern} (for example governance)")
        return aliases

    @property
    def names(self) -> dict[str, str]:
        value = self.options.get("names") or {}
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) and _NAME.match(v)
                                                  for k, v in value.items()):
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.names must map registry ids to catalog "
                               f"names matching {_NAME.pattern}")
        return value

    # -- compiled set (what Esmeralda registers itself) -----------------------------------

    def compiled_set(self, region: str) -> tuple[set[str], set[str], list[str]]:
        """Registry service ids Esmeralda's catalog compiles (to exclude), the ones it registers with
        a gateway policy or as Google API entries (expected in the registry), and the agent units
        whose engines it deploys."""
        from esmeralda.catalog.resolver import resolved_path

        path = resolved_path(self.root, self.env or "")
        try:
            compiled = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise CatalogError(f"{self.name}: can't read {path.name} to exclude what Esmeralda registers: {exc} "
                               f"(run `esmeralda catalog resolve --env {self.env} --write` first)") from exc
        google = {sid.replace("{region}", region) for sid in compiled.get("google_apis") or {}}
        expected = set(compiled.get("external_targets") or {}) | google
        for group in (compiled.get("iap_egress") or {}).values():
            expected |= set(group or {})
        services = expected | set(compiled.get("catalog") or {})
        units = sorted(e["unit"] for e in (compiled.get("catalog") or {}).values()
                       if str(e.get("unit", "")).startswith("agents/"))
        return services, expected, units

    # -- fetch (network, read-only) -------------------------------------------------------

    def fetch(self) -> list[dict[str, Any]]:
        env = self.env or ""
        region = str(self.options.get("location") or _live.env_region(self.root, env))
        outputs = _live.unit_outputs(self.root, env, PROJECTS_UNIT)
        projects = {name[: -len(PROJECT_ID_SUFFIX)]: value for name, value in sorted(outputs.items())
                    if name.endswith(PROJECT_ID_SUFFIX) and isinstance(value, str) and value}
        missing = [a for a in self.project_aliases if a not in projects]
        if missing:
            raise SourceUnreachable(f"{self.name}: no {', '.join(a + PROJECT_ID_SUFFIX for a in missing)} output in "
                                    f"{PROJECTS_UNIT} ({env}): deploy layer 1 first, or fix options.projects")
        self._placeholders = self._literals(env, region, projects)
        services, self._expected, units = self.compiled_set(region)
        self._registered = set()
        engines = set()
        for unit in units:
            engine = _live.unit_outputs(self.root, env, AGENT_UNIT.format(unit=unit)).get("engine_id")
            match = _ENGINE.search(str(engine or ""))
            if match:
                engines.add(match["id"])

        excludes = self._strings("exclude", DEFAULT_EXCLUDE)
        names = self.names
        model_hosts = set(self._strings("model_hosts", ()))
        seen: dict[str, dict[str, Any]] = {}
        for alias in self.project_aliases:
            scope = [f"--project={projects[alias]}", f"--location={region}"]
            for collection, (kind, id_field) in COLLECTIONS.items():
                items = _live.gcloud_json(self.root, ["alpha", "agent-registry", collection, "list", *scope],
                                          f"{self.name}: agent-registry {collection} list ({alias})") or []
                for item in sorted(items, key=lambda i: str(i.get(id_field) or i.get("name") or "")):
                    entry_id = str(item.get(id_field) or item.get("name") or "")
                    if not entry_id or entry_id in seen or any(fnmatch.fnmatchcase(entry_id, p) for p in excludes):
                        continue
                    reference = str(((item.get("attributes") or {}).get(RUNTIME_REFERENCE) or {}).get("uri") or "")
                    service = _SERVICE_REF.match(reference)
                    if service:
                        self._registered.add(service["sid"])
                    engine = _ENGINE.search(reference) if runtime_of(reference) == "agent_runtime" else None
                    if (service and (service["sid"] in services or service["sid"].startswith(RUNTIME_SERVICE_PREFIXES))
                            or (engine and engine["id"] in engines)):
                        continue  # Esmeralda registered or deployed it: never imported (no loop)
                    seen[entry_id] = self._entry(item, entry_id, kind, reference, service, names, model_hosts)
        return [seen[i] for i in sorted(seen)]

    def _literals(self, env: str, region: str, projects: dict[str, str]) -> Placeholders:
        numbers: dict[str, str] = {}
        ids: dict[str, str] = {}
        for alias in sorted(projects):
            if projects[alias] in ids.values():
                continue  # one placeholder per literal (a shared or BYO project)
            ids[alias] = projects[alias]
            data = _live.gcloud_json(self.root, ["projects", "describe", projects[alias]],
                                     f"{self.name}: projects describe ({alias})") or {}
            number = str(data.get("projectNumber") or "")
            if number.isdigit():
                numbers[alias] = number
        domain = _live.unit_outputs(self.root, env, NETWORK_UNIT).get("entrypoint_domain")
        return Placeholders(domain=domain or None, region=region or None, projects=ids, project_numbers=numbers)

    def _entry(self, item: dict[str, Any], entry_id: str, kind: str, reference: str, service: Any,
               names: dict[str, str], model_hosts: set[str]) -> dict[str, Any]:
        interfaces = _interfaces(item)
        if kind == "http" and interfaces and _host(interfaces[0]["url"]) in model_hosts:
            kind = "model"
        card = (item.get("card") or {}).get("content") or {}
        card = card if isinstance(card, dict) else {}
        description = " ".join(str(item.get("description") or card.get("description") or "").split())
        inventory: dict[str, Any] = {"kind": kind, "registry": {"id": entry_id}}
        if description:
            inventory["description"] = description
        if interfaces:
            inventory["deploy"] = {"endpoint": interfaces[0]["url"]}
            inventory["registry"]["interfaces"] = interfaces
        skills = _skills(item)
        if skills:
            inventory["registry"]["skills"] = skills
        runtime = runtime_of(reference)
        if runtime:
            inventory["runtime"] = runtime
            inventory["runtime_resource"] = reference
        normalized_id = normalize_placeholders(entry_id, self._placeholders)
        raw_name = service["sid"] if service else str(item.get("displayName") or card.get("name") or "")
        name = names.get(normalized_id) or derive_name(raw_name)
        if not _NAME.match(name):
            raise CatalogError(f"{self.name}: registry entry '{normalized_id}' gives no valid catalog name "
                               f"('{name}'); map it in options.names (names never get an automatic suffix)")
        return {"id": entry_id, "name": name, "inventory": inventory}

    def placeholders(self) -> Placeholders:
        return self._placeholders

    def live_findings(self) -> list[dict[str, str]]:
        """After a live fetch: registry entries the compiled catalog registers that the live registry
        doesn't list (``missing``: the registering unit wasn't applied, or the entry was deleted)."""
        region = self._placeholders.region or ""
        return [
            {"kind": "missing", "subject": f"service {sid.replace(region, '{region}') if region else sid}",
             "detail": "catalog/.resolved registers this Agent Registry entry, but the live registry of the "
                       "configured projects doesn't list it (apply the registering unit, or check options.projects)"}
            for sid in sorted(self._expected - self._registered)
        ]
