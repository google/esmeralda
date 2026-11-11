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

    expected_targets = {
        group: {n for n, e in data["catalog"].items() if e["kind"] == kind} for group, kind in IAP_GROUP_KIND.items()
    }
    assert {group: set(lists) for group, lists in data["iap_egress"].items()} == expected_targets

    for group, lists in data["iap_egress"].items():
        for name, members in lists.items():
            # Callers marked agent_gateway: false (e.g. the Apigee proxy) keep their IAM grant but
            # call the target directly, so they are never on its gateway list.
            direct = {c for c, entry in resolved.callers.items() if entry.get("agent_gateway") is False}
            callers = [c for c in data["catalog"][name].get("callers", []) if c not in direct]
            agents = [c for c in callers if c in resolved.targets]
            others = [m for m in members if m != shared.get("ref")]
            unknown = [m for m in others if m not in by_member]
            assert not unknown, f"{env} {group}.{name}: members not traced to a catalog caller: {unknown}"
            assert {by_member[m] for m in others} == set(callers), f"{env} {group}.{name}"
            for agent in agents:
                unit = resolved.targets[agent]["deploy"]["unit"]
                assert f"{unit}:agent_identity_principal" in members, f"{env} {group}.{name}: {agent} principal"
            uses_shared = bool(shared) and IAP_GROUP_KIND[group] in shared.get("kinds", []) and bool(agents)
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
            unit = data["catalog"][name]["unit"]
            assert f'config_path = "../{unit}"' in code, f"{env}: iap-egress needs a dependency on {unit}"
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
