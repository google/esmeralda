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

"""Catalog guards: the committed catalog/.resolved/<env>.json files match catalog/, and env.yaml reads them."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from esmeralda import catalog

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE = REPO_ROOT / "infrastructure/live"
LIVE_ENVS = sorted(p.parent.name for p in LIVE.glob("*/env.yaml") if p.parent.name != "shared")


def test_catalog_environments_match_live_environments():
    assert catalog.resolver.environments(REPO_ROOT) == LIVE_ENVS


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_resolved_catalog_is_up_to_date(env):
    ok, message = catalog.check_resolved(REPO_ROOT, env)
    assert ok, message


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_env_yaml_reads_the_resolved_catalog(env):
    text = (LIVE / env / "env.yaml").read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert re.search(r'^\s*catalog_sources\s*=\s*"catalog/sources\.yaml"', code, re.MULTILINE)
    expected = f'lookup(jsondecode(file("${{get_repo_root()}}/catalog/.resolved/{env}.json")), "catalog")'
    assert re.search(r"^\s*catalog\s*=\s*" + re.escape(expected) + r"\s*$", code, re.MULTILINE), (
        f"infrastructure/live/{env}/env.yaml must set catalog = {expected}"
    )
    assert not re.search(r"^\s*catalog\s*=\s*\{", code, re.MULTILINE), "inline catalog maps belong in catalog/"


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_resolved_catalog_keeps_the_unit_contract(env):
    """Every compiled entry has the shape the layer-5 units read, and its unit exists in the env."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    assert data["environment"] == env
    for name, entry in data["catalog"].items():
        assert set(entry) <= {"kind", "unit", "token", "probe", "timeout", "callers"}, name
        assert {"kind", "unit", "token", "probe"} <= set(entry), name
        assert (LIVE / env / "layer-5-workloads" / entry["unit"] / "terragrunt.hcl").is_file(), entry["unit"]


# ------------------------------------------------------------------------------------------
# Agent Gateway per-target lists (iap_egress): the catalog callers, and only them
# ------------------------------------------------------------------------------------------

IAP_GROUP_KIND = {"agents": "a2a", "mcp_servers": "mcp"}
EXTERNAL_GROUP = {"endpoint": "endpoints", "mcp_server": "mcp_servers", "agent": "agents"}
SECURITY_OUTPUTS_TF = REPO_ROOT / "infrastructure/modules/3-security/outputs.tf"


def _iap_unit_code(env: str) -> str:
    text = (LIVE / env / "layer-5-workloads/iap-egress/terragrunt.hcl").read_text()
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_env_yaml_reads_the_compiled_iap_lists(env):
    text = (LIVE / env / "env.yaml").read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    expected = f'lookup(jsondecode(file("${{get_repo_root()}}/catalog/.resolved/{env}.json")), "iap_egress")'
    assert re.search(r"^\s*catalog_iap_egress\s*=\s*" + re.escape(expected) + r"\s*$", code, re.MULTILINE)


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_compiled_iap_lists_match_catalog_callers(env):
    """Each Agent Gateway list names exactly the enforced catalog callers of its target (run.invoker set),
    minus the callers that reach it directly (agent_gateway: false)."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    resolved = catalog.resolve(REPO_ROOT, env)
    shared = (resolved.policy.get("agent_gateway") or {}).get("shared_workload_identity") or {}

    # identity ref / literal id -> caller name (agents by principal and by service account).
    by_member: dict[str, str] = {}
    for name, caller in resolved.callers.items():
        identity = caller["identity"]
        if identity["type"] == "service_account":
            by_member[identity.get("ref") or identity["id"]] = name
    for name, target in resolved.targets.items():
        if "unit" in target["deploy"]:
            by_member[f"{target['deploy']['unit']}:agent_identity_principal"] = name
            identity = target.get("identity") or {}
            if identity.get("type") == "service_account":
                by_member[identity.get("ref") or identity["id"]] = name

    # Deployed a2a / mcp targets, every managed: false target with its own registry entry, and the
    # Google API entries single agents declare (grant: callers).
    expected_targets = {
        group: {n for n, e in data["catalog"].items() if e["kind"] == kind} for group, kind in IAP_GROUP_KIND.items()
    }
    expected_targets["endpoints"] = {sid for sid, e in data["google_apis"].items() if e["grant"] == "callers"}
    for name, entry in data["external_targets"].items():
        expected_targets[EXTERNAL_GROUP[entry["registry"]]].add(name)
    assert {group: set(lists) for group, lists in data["iap_egress"].items()} == expected_targets

    def enforced_callers(name: str) -> list[str]:
        """The callers a target's grants name: agents with a unit, service-account callers."""
        if name in data["google_apis"]:
            return data["google_apis"][name]["callers"]
        if name in data["catalog"]:
            return data["catalog"][name].get("callers", [])
        out = []
        for caller in resolved.targets[name].get("callers") or {}:
            if caller in resolved.targets:
                if "unit" in resolved.targets[caller]["deploy"]:
                    out.append(caller)
            elif resolved.callers[caller]["identity"]["type"] == "service_account":
                out.append(caller)
        return out

    for group, lists in data["iap_egress"].items():
        for name, members in lists.items():
            # Callers marked agent_gateway: false (e.g. the Apigee proxy) keep their IAM grant but
            # call the target directly, so they are never on its gateway list.
            direct = {c for c, entry in resolved.callers.items() if entry.get("agent_gateway") is False}
            callers = [c for c in enforced_callers(name) if c not in direct]
            agents = [c for c in callers if c in resolved.targets]
            others = [m for m in members if m != shared.get("ref")]
            unknown = [m for m in others if m not in by_member]
            assert not unknown, f"{env} {group}.{name}: members not traced to a catalog caller: {unknown}"
            assert {by_member[m] for m in others} == set(callers), f"{env} {group}.{name}"
            for agent in agents:
                unit = resolved.targets[agent]["deploy"]["unit"]
                assert f"{unit}:agent_identity_principal" in members, f"{env} {group}.{name}: {agent} principal"
            kind = resolved.targets[name]["kind"] if name in resolved.targets else "google_api"
            uses_shared = bool(shared) and group != "endpoints" and kind in shared.get("kinds", []) and bool(agents)
            assert (shared.get("ref") in members) == uses_shared, f"{env} {group}.{name}: shared identity"


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_iap_egress_unit_can_resolve_every_compiled_ref(env):
    """Every ref maps to a unit output in the iap-egress unit, and every listed target is a dependency."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    code = _iap_unit_code(env)
    refs_block = code.split("identity_refs = merge(", 1)[1]
    security_outputs = set(re.findall(r'^output "([a-z0-9_]+)"', SECURITY_OUTPUTS_TF.read_text(), re.MULTILINE))

    for group, lists in data["iap_egress"].items():
        for name, members in lists.items():
            if name in data["catalog"]:  # deployed targets register themselves in their own unit
                unit = data["catalog"][name]["unit"]
                assert f'config_path = "../{unit}"' in code, f"{env}: iap-egress needs a dependency on {unit}"
            elif name in data["google_apis"]:  # Google API entries are registered by layer 4
                assert group == "endpoints" and data["google_apis"][name]["grant"] == "callers", name
                assert 'config_path = "../../layer-4-governance"' in code, f"{env}: iap-egress must follow layer 4"
            else:  # external targets are registered by the iap-egress unit itself
                assert name in data["external_targets"], f"{env}: {group}.{name} is neither deployed nor external"
            for member in members:
                if not re.fullmatch(r"[a-z0-9_./-]+:[a-z0-9_]+", member):
                    continue  # literal identity, passed through as is
                source, output = member.split(":")
                if source == "layer-3-security":
                    assert output in security_outputs, f"{member}: no such layer-3-security output"
                    assert output.endswith("_sa_email"), f"{member}: identity_refs only maps *_sa_email outputs"
                else:
                    assert f'"{member}"' in refs_block, f"{env}: identity_refs has no entry for {member}"
                    dep = source.rsplit("/", 1)[-1].replace("-", "_")
                    assert f'dependency "{dep}"' in code and f'config_path = "../{source}"' in code, member


# ------------------------------------------------------------------------------------------
# managed: false targets (external_targets): registered by the iap-egress unit
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_env_yaml_reads_the_compiled_external_targets(env):
    text = (LIVE / env / "env.yaml").read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    expected = f'lookup(jsondecode(file("${{get_repo_root()}}/catalog/.resolved/{env}.json")), "external_targets")'
    assert re.search(r"^\s*catalog_external_targets\s*=\s*" + re.escape(expected) + r"\s*$", code, re.MULTILINE)


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_external_targets_keep_the_unit_contract(env):
    """Every external entry has the shape the iap-egress unit reads, placeholders it can substitute,
    and a (possibly empty) caller list, never an IAM grant or an entry point route."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    for name, entry in data["external_targets"].items():
        assert set(entry) == {"kind", "registry", "url", "protocol_binding", "description", "owner_team"}, name
        assert entry["registry"] in EXTERNAL_GROUP, name
        assert entry["url"].startswith("https://"), name
        assert set(re.findall(r"\{([^}]*)\}", entry["url"])) <= {"domain", "region"}, name
        assert name in data["iap_egress"][EXTERNAL_GROUP[entry["registry"]]], name
        assert name not in data["catalog"], name
        assert "googleapis.com" not in entry["url"], f"{name}: Google APIs are layer-4 Google API entries"


# ------------------------------------------------------------------------------------------
# Google API egress (google_apis): only what the catalog declares, per agent
# ------------------------------------------------------------------------------------------

AGENTS_DIR = REPO_ROOT / "apps/agents"
GOOGLE_HOST = re.compile(r"\b([a-z0-9{}-]+(?:\.[a-z0-9{}-]+)*\.googleapis\.com)\b")


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_env_yaml_reads_the_compiled_google_apis(env):
    text = (LIVE / env / "env.yaml").read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    expected = f'lookup(jsondecode(file("${{get_repo_root()}}/catalog/.resolved/{env}.json")), "google_apis")'
    assert re.search(r"^\s*catalog_google_apis\s*=\s*" + re.escape(expected) + r"\s*$", code, re.MULTILINE)


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_google_api_entries_keep_the_layer4_contract(env):
    """Every entry has the shape layer 4 reads, a known real hostname, and at least one caller."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    known = {api.service_id: api for api in catalog.GOOGLE_APIS.values()}
    assert data["google_apis"], f"{env}: no Google API entry: agents couldn't start"
    for sid, entry in data["google_apis"].items():
        assert set(entry) == {"api", "url", "protocol_binding", "display_name", "grant", "callers"}, sid
        api = known[sid]
        assert catalog.GOOGLE_APIS[entry["api"]] == api, sid
        assert entry["url"] == f"https://{api.host}" and entry["protocol_binding"] == api.protocol_binding, sid
        assert re.fullmatch(r"https://[a-z0-9.{}-]+\.googleapis\.com", entry["url"]), sid
        assert set(re.findall(r"\{([^}]*)\}", sid + entry["url"])) <= {"region"}, sid
        assert ".mtls." not in entry["url"] and ".rep." not in entry["url"], sid
        assert entry["grant"] in ("baseline", "callers") and entry["callers"], sid


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_no_google_api_grant_without_a_catalog_declaration(env):
    """A per-agent Google API entry lists exactly the agents that declare the API, by their own identity."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    resolved = catalog.resolve(REPO_ROOT, env)
    baseline = catalog.resolver.google_api_baseline(resolved.policy)
    for sid, entry in data["google_apis"].items():
        declared_by = sorted(
            name for name, target in resolved.targets.items()
            if name in data["catalog"] and target["kind"] in ("a2a", "adk")
            and entry["api"] in (baseline + list(target.get("google_apis") or []))
        )
        assert entry["callers"] == declared_by, f"{env} {sid}"
        if entry["grant"] == "baseline":
            assert entry["api"] in baseline and sid not in data["iap_egress"]["endpoints"], sid
            continue
        members = data["iap_egress"]["endpoints"][sid]
        assert members == [f"agents/{a}:agent_identity_principal" for a in declared_by], f"{env} {sid}"
        assert not any("gcp-sa-" in m or m.startswith("principalSet:") for m in members), sid


def _agent_dirs() -> list[str]:
    data = json.loads((REPO_ROOT / "catalog/.resolved/dev.json").read_text())
    return sorted(n for n, e in data["catalog"].items() if e["kind"] in ("a2a", "adk") and (AGENTS_DIR / n).is_dir())


def _reachable_hosts(env: str, agent: str, region: str) -> set[str]:
    resolved = catalog.resolve(REPO_ROOT, env)
    apis = catalog.resolver.google_apis_of(resolved.targets[agent], resolved.policy)
    return {catalog.GOOGLE_APIS[a].host.replace("{region}", region) for a in apis}


@pytest.mark.parametrize("env", LIVE_ENVS)
@pytest.mark.parametrize("agent", _agent_dirs())
def test_every_agent_declares_the_google_apis_its_code_calls(env, agent):
    """Code signals of Google API calls (model location, sessions, telemetry, the BigQuery analytics
    plugin, literal *.googleapis.com hosts) are covered by the agent's catalog google_apis."""
    import yaml

    agent_dir = AGENTS_DIR / agent
    agent_env = yaml.safe_load((agent_dir / "agent.yaml").read_text()).get("env") or {}
    region = str(agent_env.get("GOOGLE_CLOUD_LOCATION") or "us-central1")
    reachable = _reachable_hosts(env, agent, region)

    # No mTLS entry is ever registered: the agent must pin the plain endpoints.
    assert str(agent_env.get("GOOGLE_API_USE_MTLS_ENDPOINT")).lower() == "never", agent
    assert str(agent_env.get("GOOGLE_API_USE_CLIENT_CERTIFICATE")).lower() == "false", agent

    needed: dict[str, str] = {f"{region}-aiplatform.googleapis.com": "Agent Runtime sessions"}
    location = str(agent_env.get("MODEL_LOCATION") or "global")
    model_host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    needed[model_host] = f"model calls (MODEL_LOCATION={location})"
    if "--otel-to-cloud" in (agent_dir / "Dockerfile").read_text():
        needed["telemetry.googleapis.com"] = "OpenTelemetry export"
        needed["cloudresourcemanager.googleapis.com"] = "telemetry resource detection"
    sources = "\n".join(p.read_text() for p in agent_dir.rglob("*.py") if "tests" not in p.parts)
    if "bigquery_agent_analytics_plugin" in sources:
        needed["bigquery.googleapis.com"] = "BigQuery Agent Analytics plugin"
        needed["bigquerystorage.googleapis.com"] = "BigQuery Agent Analytics plugin (AppendRows)"
        if "gcs_bucket_name" in sources:
            needed["storage.googleapis.com"] = "BigQuery Agent Analytics plugin (GCS offload)"
    for host in GOOGLE_HOST.findall(sources):
        if host != "www.googleapis.com":  # OAuth scopes, not a host the agent calls
            needed[host.replace("{region}", region)] = "literal host in the agent code"

    missing = {h: why for h, why in needed.items() if h not in reachable}
    assert not missing, f"{env} {agent}: add the matching APIs to its catalog google_apis: {missing}"
