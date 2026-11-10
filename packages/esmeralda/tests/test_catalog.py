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

# The inline env.yaml `catalog` map before it moved to catalog/ (dev and prd were identical).
LEGACY_MAP = {
    "legacy-dms": {"kind": "mcp", "unit": "services/legacy-dms", "token": "id", "probe": "mcp",
                   "callers": ["ai-coe-mortgage-specialist", "cx-mortgage-orchestrator", "test-vm"]},
    "income-verification": {"kind": "mcp", "unit": "services/income-verification", "token": "id", "probe": "mcp",
                            "callers": ["ai-coe-mortgage-specialist", "cx-mortgage-orchestrator", "test-vm"]},
    "corporate-email": {"kind": "mcp", "unit": "services/corporate-email", "token": "id", "probe": "mcp",
                        "callers": ["ai-coe-mortgage-specialist", "cx-mortgage-orchestrator", "test-vm"]},
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
    assert compiled["legacy-dms"]["callers"] == ["ai-coe-mortgage-specialist", "cx-mortgage-orchestrator"]
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
