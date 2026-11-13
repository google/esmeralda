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

"""Agent Registry egress check: every registry entry holds exactly the roles/iap.egressor list it should.

Read-only (``gcloud ... list`` / ``get-iam-policy``). Compares the live central Agent Registry with
what the layers compiled:

* the registry root holds no binding (a root binding is inherited by every entry);
* each system endpoint entry (layer 4 ``system_endpoint_ids``) lists exactly ``iap_egress_members``;
* each agent, external endpoint and MCP server entry of the catalog (``iap-egress`` unit output
  ``target_iap_members``) lists exactly its callers, an empty list meaning an empty policy;
* each external target (``external_registry_entries``) is registered with its exact URL and type;
* any other entry holds no policy at all (nobody can reach it through the Agent Gateway).

This is the policy-level egress probe: a listed caller is on the entry's policy and a caller
that isn't listed is on no policy that covers the entry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
from esmeralda.ops.repo import OpsError

ROLE = "roles/iap.egressor"
L1 = "layer-1-projects"
L4 = "layer-4-governance"
IAP_UNIT = "layer-5-workloads/iap-egress"
# IAP resource collection of each registry entry type, and the gcloud flag that names one entry.
FLAGS = {"endpoints": "--endpoint", "agents": "--agent", "mcpServers": "--mcp-server"}
GROUP_COLLECTION = {"agents": "agents", "endpoints": "endpoints", "mcp_servers": "mcpServers"}
REGISTRY_COLLECTION = {"endpoint": "endpoints", "agent": "agents", "mcp_server": "mcpServers"}


def egress_members(policy: dict[str, Any]) -> list[str] | None:
    """Sorted ``roles/iap.egressor`` members of a policy; ``None`` when another binding is present."""
    members: list[str] = []
    for binding in policy.get("bindings") or []:
        if not binding.get("members"):
            continue
        if binding.get("role") != ROLE or binding.get("condition"):
            return None
        members += binding["members"]
    return sorted(set(members))


def entry_urls(service: dict[str, Any]) -> list[str]:
    """Interface URLs of a registry service (agent entries: the card's supportedInterfaces)."""
    urls = [i.get("url") for i in service.get("interfaces") or [] if i.get("url")]
    content = (service.get("agentSpec") or {}).get("content") or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    urls += [i.get("url") for i in content.get("supportedInterfaces") or [] if i.get("url")]
    return urls


def index_services(services: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``service_id -> {collection, uid, urls}`` for every registered entry with a registry resource."""
    out = {}
    for service in services:
        resource = service.get("registryResource") or ""
        parts = resource.split("/")
        if len(parts) < 2:
            continue
        out[service["name"].rsplit("/", 1)[-1]] = {
            "collection": parts[-2],
            "uid": parts[-1],
            "urls": entry_urls(service),
        }
    return out


def evaluate(
    *,
    services: list[dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    root_policy: dict[str, Any],
    system_ids: list[str],
    system_members: list[str],
    target_members: dict[str, dict[str, list[str]]],
    external_entries: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """The checks, from the live registry (``services``, ``policies`` by service id, root policy)."""
    index = index_services(services)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    root = egress_members(root_policy)
    add("registry root", root == [], "no binding" if root == [] else
        f"holds a binding ({json.dumps(root_policy.get('bindings'))}): every entry it matches inherits it")

    expected: dict[str, tuple[str, list[str]]] = {sid: ("endpoints", sorted(set(system_members))) for sid in system_ids}
    for group, targets in target_members.items():
        for sid, members in targets.items():
            expected[sid] = (GROUP_COLLECTION[group], sorted(set(members)))

    for sid, entry in sorted(external_entries.items()):
        found = index.get(sid)
        want = REGISTRY_COLLECTION.get(entry["registry"], entry["registry"])
        if not found:
            add(f"external {sid}", False, f"not registered (expected {want} entry for {entry['url']})")
        elif found["collection"] != want or entry["url"] not in found["urls"]:
            add(f"external {sid}", False, f"registered as {found['collection']} {found['urls']}, expected {want} "
                f"[{entry['url']}]")
        else:
            add(f"external {sid}", True, f"{want} entry for {entry['url']}")

    for sid in sorted(set(expected) | set(index)):
        found = index.get(sid)
        if sid not in index:
            add(f"entry {sid}", False, "listed in the compiled policies but not registered")
            continue
        policy = policies.get(sid)
        if policy is None:
            add(f"entry {sid}", False, "could not read its IAP policy")
            continue
        have = egress_members(policy)
        if sid in expected:
            collection, want = expected[sid]
            if found["collection"] != collection:
                add(f"entry {sid}", False, f"is a {found['collection']} entry, expected {collection}")
            elif have != want:
                add(f"entry {sid}", False, f"policy lists {have}, expected exactly {want}")
            else:
                add(f"entry {sid}", True, f"{len(want)} member(s), as compiled" if want else "empty policy, as compiled")
        else:
            add(f"entry {sid}", have == [], "not in any compiled list: no policy" if have == [] else
                f"not in any compiled list, but its policy lists {have}")
    return checks


def _gcloud_json(root: Path, args: list[str], what: str) -> Any:
    result = repo.run(["gcloud", *args, "--format=json"], cwd=root, timeout=120, what=what)
    if result.returncode != 0:
        raise OpsError(f"{what} failed: {repo.short(result.stderr, 300)}")
    return json.loads(result.stdout or "null")


def check(root: Path, env: str, *, workers: int = 8, gcloud: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Runs the checks against the deployed environment (read-only)."""
    run = gcloud or _gcloud_json
    project = repo.output(root, env, L1, "governance_project_id")
    region = repo._hcl_string(repo.env_locals(root, env), "region")
    system_ids = repo.output(root, env, L4, "system_endpoint_ids")
    system_members = repo.output(root, env, L4, "iap_egress_members")
    target_members = repo.output(root, env, IAP_UNIT, "target_iap_members")
    external = repo.output(root, env, IAP_UNIT, "external_registry_entries") or {}
    if not project or not region:
        raise OpsError(f"No governance project or region for {env} (deploy layer 1 first)")
    if system_ids is None or system_members is None:
        raise OpsError(f"No system_endpoint_ids output in {L4} ({env}): apply layer 4 governance first")
    if target_members is None:
        raise OpsError(f"No target_iap_members output in {IAP_UNIT} ({env}): apply the iap-egress unit first")

    scope = [f"--project={project}", f"--location={region}"]
    services = run(root, ["alpha", "agent-registry", "services", "list", *scope], "agent-registry services list") or []
    index = index_services(services)
    iap = [f"--project={project}", "--resource-type=agent-registry", f"--region={region}"]
    root_policy = run(root, ["iap", "web", "get-iam-policy", *iap], "registry root IAP policy") or {}

    def fetch(sid: str) -> tuple[str, dict[str, Any] | None]:
        entry = index[sid]
        flag = FLAGS.get(entry["collection"])
        if not flag:
            return sid, None
        try:
            return sid, run(root, ["iap", "web", "get-iam-policy", *iap, f"{flag}={entry['uid']}"],
                            f"IAP policy of {sid}") or {}
        except OpsError:
            return sid, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        policies = dict(pool.map(fetch, sorted(index)))

    checks = evaluate(
        services=services,
        policies=policies,
        root_policy=root_policy,
        system_ids=system_ids,
        system_members=system_members,
        target_members=target_members,
        external_entries=external,
    )
    failed = [c for c in checks if not c["ok"]]
    return {
        "env": env,
        "passed": not failed,
        "ok": len(checks) - len(failed),
        "failed": len(failed),
        "checks": checks,
    }
