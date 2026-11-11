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

"""Catalog resolver, compiler and CLI (offline)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from esmeralda import catalog, cli
from esmeralda.catalog import CatalogError

REPO = Path(__file__).resolve().parents[3]

# The inline env.yaml `catalog` map before it moved to catalog/ (dev and prd were identical), minus
# cx-mortgage-orchestrator on the MCP servers: it only calls the specialist (A2A), so the catalog
# no longer lists it there (it was never on their Agent Gateway lists either).
LEGACY_MAP = {
    "legacy-dms": {"kind": "mcp", "unit": "services/legacy-dms", "token": "id", "probe": "mcp",
                   "callers": ["ai-coe-mortgage-specialist", "test-vm"]},
    "income-verification": {"kind": "mcp", "unit": "services/income-verification", "token": "id", "probe": "mcp",
                            "callers": ["ai-coe-mortgage-specialist", "test-vm"]},
    "corporate-email": {"kind": "mcp", "unit": "services/corporate-email", "token": "id", "probe": "mcp",
                        "callers": ["ai-coe-mortgage-specialist", "test-vm"]},
    "ai-coe-mortgage-specialist": {"kind": "a2a", "unit": "agents/ai-coe-mortgage-specialist", "token": "access",
                                   "probe": "a2a", "callers": ["cx-mortgage-orchestrator", "test-vm"]},
    "cx-mortgage-orchestrator": {"kind": "adk", "unit": "agents/cx-mortgage-orchestrator", "token": "access",
                                 "probe": "query", "timeout": 600},
}

# dev also lets the Apigee partner-mcp proxy call the MCP servers it exposes (catalog/environments/dev.yaml).
DEV_MAP = {
    name: (
        {**entry, "callers": [*entry["callers"], "apigee-partner-mcp"]}
        if name in ("income-verification", "corporate-email")
        else entry
    )
    for name, entry in LEGACY_MAP.items()
}
ENV_MAPS = {"dev": DEV_MAP, "prd": LEGACY_MAP}

# The per-target Agent Gateway lists the iap-egress units used to hand-write, as catalog refs.
_SPECIALIST_ON_MCP = [
    "agents/ai-coe-mortgage-specialist:agent_identity_principal",
    "layer-3-security:ai_coe_mortgage_specialist_sa_email",
    "layer-3-security:mcp_invoker_sa_email",
    "layer-3-security:test_vm_sa_email",
]
LEGACY_IAP_EGRESS = {
    "agents": {
        "ai-coe-mortgage-specialist": [
            "agents/cx-mortgage-orchestrator:agent_identity_principal",
            "layer-3-security:cx_mortgage_orchestrator_sa_email",
            "layer-3-security:test_vm_sa_email",
        ],
    },
    "mcp_servers": {
        "corporate-email": _SPECIALIST_ON_MCP,
        "income-verification": _SPECIALIST_ON_MCP,
        "legacy-dms": _SPECIALIST_ON_MCP,
    },
}

HEADER = "schema: esmeralda.catalog\nschema_version: 1\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of the repository's catalog/ and contracts/ to mutate."""
    shutil.copytree(REPO / "catalog", tmp_path / "catalog")
    (tmp_path / "contracts").mkdir()
    shutil.copy(REPO / "contracts" / "catalog.schema.json", tmp_path / "contracts")
    return tmp_path


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "catalog" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + body)


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


# ------------------------------------------------------------------------------------------
# the repository catalog
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_repo_catalog_compiles_to_the_legacy_env_yaml_map(env):
    resolved = catalog.resolve(REPO, env)
    compiled = catalog.compile_terraform(resolved)
    assert compiled == ENV_MAPS[env]
    # Caller order is preserved (list attributes in the units).
    assert compiled["legacy-dms"]["callers"] == LEGACY_MAP["legacy-dms"]["callers"]


def test_repo_catalog_resolved_files_are_up_to_date():
    for env in catalog.resolver.environments(REPO):
        ok, message = catalog.check_resolved(REPO, env)
        assert ok, message


def test_examples_are_resolved_but_not_compiled():
    resolved = catalog.resolve(REPO, "dev")
    assert resolved.targets["example-core-banking"]["example"] is True
    assert resolved.callers["aws-underwriter"]["identity"]["type"] == "federated"
    assert resolved.teams["example-partner"]["external"] is True
    assert "example-core-banking" not in catalog.compile_terraform(resolved)
    assert "gemini-flash" not in catalog.compile_terraform(resolved)


def test_policy_defaults_fill_the_resolved_view_only():
    resolved = catalog.resolve(REPO, "dev")
    assert resolved.targets["legacy-dms"]["gateway"] == "direct"
    assert resolved.targets["legacy-dms"]["user_credential"] == {"accept": False}
    # Explicit values win over defaults.
    assert resolved.targets["ai-coe-mortgage-specialist"]["observability"]["usage_sources"] == ["gateway", "workload"]
    assert resolved.targets["ai-coe-mortgage-specialist"]["observability"]["sampling"] == 1.0
    assert "gateway" not in catalog.compile_terraform(resolved)["legacy-dms"]


def test_render_hcl_matches_the_former_inline_form():
    hcl = catalog.render_hcl(catalog.resolve(REPO, "dev"))
    assert hcl.startswith("catalog = {\n")
    assert (
        '  cx-mortgage-orchestrator   = { kind = "adk", unit = "agents/cx-mortgage-orchestrator", token = "access", '
        'probe = "query", timeout = 600 }' in hcl
    )
    assert 'callers = ["cx-mortgage-orchestrator", "test-vm"]' in hcl


# ------------------------------------------------------------------------------------------
# Agent Gateway per-target lists (iap_egress)
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_repo_catalog_compiles_the_former_hand_written_iap_lists(env):
    """Same members, same order as the lists the iap-egress units used to hand-write."""
    resolved = catalog.resolve(REPO, env)
    assert catalog.compile_iap_egress(resolved) == LEGACY_IAP_EGRESS
    data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
    assert data["iap_egress"] == LEGACY_IAP_EGRESS


def test_iap_lists_keep_least_privilege():
    iap = catalog.compile_iap_egress(catalog.resolve(REPO, "dev"))
    for members in iap["mcp_servers"].values():
        assert not any("cx-mortgage-orchestrator" in m or "cx_mortgage_orchestrator" in m for m in members)
    for name, members in iap["agents"].items():
        assert not any(name in m or name.replace("-", "_") in m for m in members), name


def test_adding_a_caller_updates_invokers_and_gateway_lists(repo):
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("  test-vm: {}\n", "  test-vm: {}\n  cx-mortgage-orchestrator: {}\n"))
    resolved = catalog.resolve(repo, "dev")
    assert catalog.compile_terraform(resolved)["legacy-dms"]["callers"] == [
        "ai-coe-mortgage-specialist", "test-vm", "cx-mortgage-orchestrator"
    ]
    # Agent callers first (principal, then service account), then the shared identity, then the rest.
    assert catalog.compile_iap_egress(resolved)["mcp_servers"]["legacy-dms"] == [
        "agents/ai-coe-mortgage-specialist:agent_identity_principal",
        "layer-3-security:ai_coe_mortgage_specialist_sa_email",
        "agents/cx-mortgage-orchestrator:agent_identity_principal",
        "layer-3-security:cx_mortgage_orchestrator_sa_email",
        "layer-3-security:mcp_invoker_sa_email",
        "layer-3-security:test_vm_sa_email",
    ]
    # Other targets are untouched.
    assert catalog.compile_iap_egress(resolved)["mcp_servers"]["corporate-email"] == _SPECIALIST_ON_MCP


def test_shared_workload_identity_comes_from_the_policy(repo):
    _write(repo, "environments/prd.yaml",
           "environment: prd\noverrides:\n  policy:\n    agent_gateway: null\n")
    iap = catalog.compile_iap_egress(catalog.resolve(repo, "prd"))
    assert iap["mcp_servers"]["legacy-dms"] == [m for m in _SPECIALIST_ON_MCP if "mcp_invoker" not in m]
    # Only added where an agent calls: a target with only non-agent callers never gets it.
    _write(repo, "environments/dev.yaml",
           "environment: dev\noverrides:\n  targets:\n    legacy-dms:\n"
           "      callers: { ai-coe-mortgage-specialist: null }\n")
    iap = catalog.compile_iap_egress(catalog.resolve(repo, "dev"))
    assert iap["mcp_servers"]["legacy-dms"] == ["layer-3-security:test_vm_sa_email"]


def test_shared_workload_identity_kinds_are_honoured(repo):
    path = repo / "catalog/policy.yaml"
    path.write_text(path.read_text().replace("kinds: [mcp]", "kinds: [mcp, a2a]"))
    iap = catalog.compile_iap_egress(catalog.resolve(repo, "dev"))
    assert iap["agents"]["ai-coe-mortgage-specialist"] == [
        "agents/cx-mortgage-orchestrator:agent_identity_principal",
        "layer-3-security:cx_mortgage_orchestrator_sa_email",
        "layer-3-security:mcp_invoker_sa_email",
        "layer-3-security:test_vm_sa_email",
    ]


def test_iap_lists_skip_unenforced_callers_and_keep_literal_ids(repo):
    _write(repo, "teams/platform/more-callers.yaml",
           "callers:\n  batch-job:\n    team: platform\n"
           "    identity: { type: service_account, id: batch-job@example.iam.gserviceaccount.com }\n")
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("  test-vm: {}\n", "  test-vm: {}\n  platform-ops: {}\n  batch-job: {}\n"))
    resolved = catalog.resolve(repo, "dev")
    assert catalog.compile_terraform(resolved)["legacy-dms"]["callers"] == [
        "ai-coe-mortgage-specialist", "test-vm", "batch-job"
    ]
    assert catalog.compile_iap_egress(resolved)["mcp_servers"]["legacy-dms"] == [
        *_SPECIALIST_ON_MCP, "batch-job@example.iam.gserviceaccount.com"
    ]


def test_direct_callers_keep_iam_but_are_never_on_gateway_lists():
    """apigee-partner-mcp (agent_gateway: false) calls the MCP servers directly, not through the gateway."""
    resolved = catalog.resolve(REPO, "dev")
    assert resolved.callers["apigee-partner-mcp"]["agent_gateway"] is False
    compiled = catalog.compile_terraform(resolved)
    iap = catalog.compile_iap_egress(resolved)
    for name in ("corporate-email", "income-verification"):
        assert "apigee-partner-mcp" in compiled[name]["callers"]  # run.invoker
        assert iap["mcp_servers"][name] == _SPECIALIST_ON_MCP  # but no roles/iap.egressor
    assert not any("apigee" in m for lists in iap.values() for members in lists.values() for m in members)


def test_gateway_caller_with_a_ref_the_iap_unit_cannot_map_fails(repo):
    path = repo / "catalog/teams/platform/callers.yaml"
    path.write_text(path.read_text().replace("    agent_gateway: false\n", ""))
    problems = _problems(repo)
    assert "caller 'apigee-partner-mcp' would go on the Agent Gateway list" in problems
    assert "agent_gateway: false" in problems
    # prd doesn't list that caller on any target, so it still resolves there.
    catalog.resolve(repo, "prd")


def test_iap_lists_cover_only_registry_targets():
    iap = catalog.compile_iap_egress(catalog.resolve(REPO, "dev"))
    assert set(iap) == {"agents", "mcp_servers"}
    # adk agents (the orchestrator), examples and models have no Agent Registry entry of their own.
    assert "cx-mortgage-orchestrator" not in iap["agents"]
    assert "example-core-banking" not in iap["mcp_servers"] and "gemini-flash" not in iap["mcp_servers"]


def test_show_answers_who_calls_via_which_gateway_and_who_pays():
    resolved = catalog.resolve(REPO, "dev")
    data = catalog.show(resolved, target="ai-coe-mortgage-specialist", ingress={"default": "direct", "host_rules": {}})
    (row,) = data["targets"]
    assert row["ingress_backend"] == "direct"
    by_caller = {c["caller"]: c for c in row["callers"]}
    assert by_caller["cx-mortgage-orchestrator"]["pays"] == {"team": "cx", "cost_centers": ["cc-cx-br", "cc-cx-mx"]}
    assert by_caller["test-vm"]["identity"] == "service_account"
    assert by_caller["test-vm"]["pays"]["team"] == "platform"
    assert all(c["enforced"] for c in row["callers"])

    what_vm_calls = catalog.show(resolved, caller="test-vm")
    assert {r["target"] for r in what_vm_calls["targets"]} == {
        "legacy-dms", "income-verification", "corporate-email", "ai-coe-mortgage-specialist"
    }
    with pytest.raises(CatalogError):
        catalog.show(resolved, target="nope")


# ------------------------------------------------------------------------------------------
# validation failures
# ------------------------------------------------------------------------------------------


def test_unknown_caller_fails(repo):
    _write(repo, "teams/cx/agents/x-agent.yaml",
           "name: x-agent\nkind: a2a\nowner_team: cx\ndeploy: { unit: agents/x-agent }\n"
           "serving: { token: access, probe: a2a }\ncallers: { ghost: {} }\n")
    assert "unknown caller 'ghost'" in _problems(repo)


def test_unknown_team_fails(repo):
    _write(repo, "teams/cx/agents/x-agent.yaml",
           "name: x-agent\nkind: a2a\nowner_team: nobody\ndeploy: { unit: agents/x-agent }\n"
           "serving: { token: access, probe: a2a }\n")
    assert "unknown owner_team 'nobody'" in _problems(repo)


def test_unknown_dimension_fails(repo):
    _write(repo, "teams/cx/agents/x-agent.yaml",
           "name: x-agent\nkind: a2a\nowner_team: cx\ndeploy: { unit: agents/x-agent }\n"
           "serving: { token: access, probe: a2a }\nobservability: { dimensions: [branch_code] }\n")
    assert "unknown dimension 'branch_code'" in _problems(repo)


def test_duplicate_target_across_files_fails(repo):
    shutil.copy(repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml", repo / "catalog/teams/cx/legacy-dms-copy.yaml")
    assert "duplicate target 'legacy-dms'" in _problems(repo)


def test_duplicate_yaml_key_fails(repo):
    _write(repo, "teams/cx/callers.yaml",
           "callers:\n  kiosk: { team: cx, identity: { type: group, id: a@example.com } }\n"
           "  kiosk: { team: cx, identity: { type: group, id: b@example.com } }\n")
    assert "duplicate key 'kiosk'" in _problems(repo)


def test_caller_and_target_with_the_same_name_fail(repo):
    _write(repo, "teams/cx/callers.yaml",
           "callers:\n  legacy-dms: { team: cx, identity: { type: group, id: a@example.com } }\n")
    assert "duplicate name 'legacy-dms'" in _problems(repo)


def test_duplicate_policy_leaf_fails(repo):
    _write(repo, "external/policy-extra.yaml", "policy:\n  attribution: { unmapped_caller: flag }\n")
    assert "duplicate policy.attribution.unmapped_caller" in _problems(repo)


def test_cost_centre_outside_the_callers_team_fails(repo):
    path = repo / "catalog/teams/ai-coe/agents/ai-coe-mortgage-specialist.yaml"
    path.write_text(path.read_text().replace("[cc-cx-br, cc-cx-mx]", "[cc-cx-br, cc-ai-coe]"))
    assert "may not bill cost centre 'cc-ai-coe'" in _problems(repo)


def test_unit_must_match_target_name(repo):
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("unit: services/legacy-dms", "unit: services/dms"))
    assert "must end with the target name" in _problems(repo)


def test_schema_violation_names_the_file(repo):
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("kind: mcp", "kind: grpc"))
    problems = _problems(repo)
    assert "catalog/teams/ai-coe/mcp/legacy-dms.yaml" in problems


def test_wrong_header_fails(repo):
    path = repo / "catalog/teams.yaml"
    path.write_text(path.read_text().replace("schema_version: 1", "schema_version: 2"))
    assert "schema_version: 1" in _problems(repo)


def test_unknown_environment_fails(repo):
    assert "unknown environment 'stg'" in _problems(repo, "stg")


# ------------------------------------------------------------------------------------------
# sources and overrides
# ------------------------------------------------------------------------------------------


def test_environment_overrides_merge_maps_and_null_deletes(repo):
    _write(repo, "environments/prd.yaml",
           "environment: prd\noverrides:\n  targets:\n    legacy-dms:\n      callers: { test-vm: null }\n"
           "    cx-mortgage-orchestrator:\n      serving: { timeout: 900 }\n")
    compiled = catalog.compile_terraform(catalog.resolve(repo, "prd"))
    assert compiled["legacy-dms"]["callers"] == ["ai-coe-mortgage-specialist"]
    assert compiled["cx-mortgage-orchestrator"]["timeout"] == 900
    assert compiled["cx-mortgage-orchestrator"]["token"] == "access"  # merged, not replaced
    assert catalog.compile_terraform(catalog.resolve(repo, "dev")) == DEV_MAP


def test_override_is_validated(repo):
    _write(repo, "environments/dev.yaml",
           "environment: dev\noverrides:\n  targets:\n    legacy-dms:\n      callers: { ghost: {} }\n")
    assert "unknown caller 'ghost'" in _problems(repo)


def test_stub_sources_are_extension_points(repo):
    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("type: agent_registry\n    enabled: false", "type: agent_registry"))
    with pytest.raises(NotImplementedError, match="agent_registry"):
        catalog.resolve(repo, "dev")


def test_registered_adapter_fills_unset_fields_and_conflicts_fail(repo, monkeypatch):
    from esmeralda.catalog import Layer, Source, register_source
    from esmeralda.catalog import sources as catalog_sources

    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("test_inventory")
    class Inventory(Source):
        def load(self) -> Layer:
            layer = Layer(source=self.name)
            layer.targets["legacy-dms"] = {"description": "from inventory", **self.spec.get("options", {})}
            return layer

    path = repo / "catalog/sources.yaml"
    text = path.read_text().replace(
        "  # Inventory from Agent Registry",
        "  - name: inventory\n    type: test_inventory\n\n  # Inventory from Agent Registry",
    )
    path.write_text(text)
    # legacy-dms already has a description in the files source: conflicting value.
    assert "conflicting values" in _problems(repo)

    target = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    target.write_text("\n".join(line for line in target.read_text().splitlines() if not line.startswith("description")))
    resolved = catalog.resolve(repo, "dev")
    assert resolved.targets["legacy-dms"]["description"] == "from inventory"
    assert resolved.document["sources"] == ["catalog-files", "inventory", "environments/dev.yaml"]


def test_check_resolved_detects_stale_file(repo):
    ok, _ = catalog.check_resolved(repo, "dev")
    assert ok
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("  test-vm: {}\n", ""))
    ok, message = catalog.check_resolved(repo, "dev")
    assert not ok and "out of date" in message
    catalog.write_resolved(repo, "dev")
    assert catalog.check_resolved(repo, "dev")[0]
    data = json.loads((repo / "catalog/.resolved/dev.json").read_text())
    assert "test-vm" not in data["catalog"]["legacy-dms"]["callers"]


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------


def test_cli_resolve_check_and_json(capsys):
    assert cli.main(["catalog", "resolve", "--env", "all", "--check", "--repo", str(REPO)]) == 0
    capsys.readouterr()
    assert cli.main(["catalog", "resolve", "--env", "dev", "--repo", str(REPO)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "esmeralda.catalog" and doc["environment"] == "dev"
    assert cli.main(["catalog", "resolve", "--env", "dev", "--format", "hcl", "--repo", str(REPO)]) == 0
    assert capsys.readouterr().out.startswith("catalog = {")


def test_cli_resolve_terraform_format_equals_committed_file(capsys):
    assert cli.main(["catalog", "resolve", "--env", "prd", "--format", "terraform", "--repo", str(REPO)]) == 0
    assert capsys.readouterr().out == (REPO / "catalog/.resolved/prd.json").read_text()


def test_cli_show_json_and_text(capsys):
    assert cli.main(["catalog", "show", "--env", "dev", "--json", "--repo", str(REPO)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {t["target"] for t in data["targets"]} >= set(LEGACY_MAP)
    assert cli.main(["catalog", "show", "--env", "dev", "--target", "legacy-dms", "--repo", str(REPO)]) == 0
    out = capsys.readouterr().out
    assert "legacy-dms" in out and "pays: platform" in out


def test_cli_reports_catalog_errors(capsys):
    assert cli.main(["catalog", "show", "--env", "stg", "--repo", str(REPO)]) == 2
    assert "unknown environment 'stg'" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------
# verified attribution (principal_teams, caller allowlists, policy)
# ------------------------------------------------------------------------------------------


def test_principal_teams_maps_every_verified_identity_to_its_team():
    rows = {r["principal"]: r for r in catalog.compile_principal_teams(catalog.resolve(REPO, "dev"))}
    assert rows["agents/cx-mortgage-orchestrator:agent_identity_principal"] == {
        "principal": "agents/cx-mortgage-orchestrator:agent_identity_principal",
        "caller": "cx-mortgage-orchestrator", "team": "cx", "cost_centers": ["cc-cx-br", "cc-cx-mx"],
        "verified_by": "spiffe",
    }
    assert rows["layer-3-security:ai_coe_mortgage_specialist_sa_email"]["team"] == "ai-coe"
    assert rows["layer-3-security:test_vm_sa_email"]["verified_by"] == "service_account"
    assert rows["group:platform-ops@example.com"] == {
        "principal": "group:platform-ops@example.com", "caller": "platform-ops", "team": "platform",
        "cost_centers": ["cc-platform"], "verified_by": "group",
    }
    # The shared invoker identity names no single team (several agents present it).
    assert "layer-3-security:mcp_invoker_sa_email" not in rows
    # Examples never become attribution rows.
    assert not any(r["caller"] == "aws-underwriter" for r in rows.values())


def test_attribution_block_is_in_the_committed_resolved_files():
    for env in ("dev", "prd"):
        data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
        resolved = catalog.resolve(REPO, env)
        assert data["attribution"] == catalog.compile_attribution(resolved)
        assert data["attribution"]["shared_principals"] == ["layer-3-security:mcp_invoker_sa_email"]
    dev = json.loads((REPO / "catalog/.resolved/dev.json").read_text())["attribution"]
    prd = json.loads((REPO / "catalog/.resolved/prd.json").read_text())["attribution"]
    # Rollout: dev flags unknown callers first; prd always denies.
    assert dev["policy"] == {"unmapped_caller": "flag", "declared_mismatch": "reject"}
    assert prd["policy"] == {"unmapped_caller": "deny", "declared_mismatch": "reject"}


def test_caller_allowlists_follow_the_iam_callers():
    resolved = catalog.resolve(REPO, "dev")
    allowlists = catalog.compile_caller_allowlists(resolved)
    assert set(allowlists) == {"legacy-dms", "income-verification", "corporate-email", "ai-coe-mortgage-specialist"}
    by_principal = {e["principal"]: e for e in allowlists["corporate-email"]}
    assert by_principal["layer-5-workloads/apigee/platform:proxy_runtime_sa_email"]["caller"] == "apigee-partner-mcp"
    # Shared invoker on MCP targets: every agent caller is in ai-coe, so the team resolves.
    shared = by_principal["layer-3-security:mcp_invoker_sa_email"]
    assert shared == {"principal": "layer-3-security:mcp_invoker_sa_email", "caller": "ai-coe-mortgage-specialist",
                      "team": "ai-coe", "cost_centers": ["cc-ai-coe"], "shared": True}
    # The specialist (a2a) is not of a shared kind: the orchestrator's grant limits its cost centres.
    spec = {e["principal"]: e for e in allowlists["ai-coe-mortgage-specialist"]}
    assert "layer-3-security:mcp_invoker_sa_email" not in spec
    assert spec["layer-3-security:cx_mortgage_orchestrator_sa_email"]["cost_centers"] == ["cc-cx-br", "cc-cx-mx"]
    # Groups get no IAM grant, so no allowlist entry; the ADK orchestrator has no guard.
    assert "cx-mortgage-orchestrator" not in allowlists


def test_shared_identity_across_teams_is_verified_without_a_team(repo):
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("  test-vm: {}\n", "  test-vm: {}\n  cx-mortgage-orchestrator: {}\n"))
    allowlists = catalog.compile_caller_allowlists(catalog.resolve(repo, "dev"))
    (shared,) = [e for e in allowlists["legacy-dms"] if e.get("shared")]
    assert shared["team"] is None and shared["cost_centers"] == []
    assert shared["caller"] == "ai-coe-mortgage-specialist+cx-mortgage-orchestrator"


def test_unmapped_caller_flag_is_refused_outside_flag_environments(repo):
    _write(repo, "environments/prd.yaml",
           "environment: prd\noverrides:\n  policy:\n    attribution: { unmapped_caller: flag }\n")
    assert "'flag' is not allowed in environment 'prd'" in _problems(repo, "prd")
    path = repo / "catalog/policy.yaml"
    path.write_text(path.read_text().replace("flag_environments: [dev]", "flag_environments: [dev, prd]"))
    assert catalog.resolve(repo, "prd").policy["attribution"]["unmapped_caller"] == "flag"


def test_principal_mapped_to_two_teams_fails(repo):
    _write(repo, "teams/cx/callers.yaml",
           "callers:\n  cx-batch:\n    team: cx\n"
           "    identity: { type: service_account, ref: \"layer-3-security:test_vm_sa_email\" }\n")
    with pytest.raises(CatalogError) as exc:
        catalog.compile_principal_teams(catalog.resolve(repo, "dev"))
    message = str(exc.value)
    assert "principal 'layer-3-security:test_vm_sa_email' maps to team" in message
    assert "'cx' (caller 'cx-batch')" in message and "'platform' (caller 'test-vm')" in message


def test_unregistered_probe_identity_is_never_compiled():
    """The verify probe identity must stay out of every catalog-compiled binding and table."""
    for env in ("dev", "prd"):
        text = (REPO / "catalog/.resolved" / f"{env}.json").read_text()
        assert "probe_unregistered" not in text and "probe-unregistered" not in text
    for path in (REPO / "catalog").rglob("*.yaml"):
        assert "probe_unregistered" not in path.read_text(), path
