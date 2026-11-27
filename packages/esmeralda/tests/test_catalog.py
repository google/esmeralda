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

# The per-target Agent Gateway lists, as catalog refs. Agents call out with their own Agent Identity
# (no shared workload identity, no agent service account).
_SPECIALIST_ON_MCP = [
    "agents/ai-coe-mortgage-specialist:agent_identity_principal",
    "layer-3-security:test_vm_sa_email",
]
_SPECIALIST = ["agents/ai-coe-mortgage-specialist:agent_identity_principal"]
_BOTH_AGENTS = [*_SPECIALIST, "agents/cx-mortgage-orchestrator:agent_identity_principal"]
# The Google APIs single agents declare (catalog google_apis, beyond the policy baseline): each
# entry lists only the agents that declare it.
GOOGLE_API_IAP = {
    "aiplatform": _BOTH_AGENTS,
    "bigquery": _SPECIALIST,
    "bigquerystorage": _SPECIALIST,
    "storage": _SPECIALIST,
}
LEGACY_IAP_EGRESS = {
    "agents": {
        "ai-coe-mortgage-specialist": [
            "agents/cx-mortgage-orchestrator:agent_identity_principal",
            "layer-3-security:test_vm_sa_email",
        ],
    },
    "mcp_servers": {
        "corporate-email": _SPECIALIST_ON_MCP,
        "income-verification": _SPECIALIST_ON_MCP,
        "legacy-dms": _SPECIALIST_ON_MCP,
    },
    "endpoints": GOOGLE_API_IAP,
}
# dev also declares the synthetic external target external-probe and, with model_gateway = "apigee",
# the Apigee model gateway llm-gateway (catalog/environments/dev.yaml).
_AGENTS = ["agents/cx-mortgage-orchestrator:agent_identity_principal", *_SPECIALIST]
ENV_IAP_EGRESS = {
    "prd": LEGACY_IAP_EGRESS,
    "dev": {
        **LEGACY_IAP_EGRESS,
        "endpoints": {**GOOGLE_API_IAP, "external-probe": _SPECIALIST, "llm-gateway": _AGENTS},
    },
}
# The Google API entries layer 4 registers (8, from 91 hostname variants before): the baseline
# (every agent runtime) and the per-agent ones.
GOOGLE_API_ENTRIES = {
    "{region}-aiplatform": ("baseline", "https://{region}-aiplatform.googleapis.com", "HTTP_JSON"),
    "telemetry": ("baseline", "https://telemetry.googleapis.com", "HTTP_JSON"),
    "cloudresourcemanager": ("baseline", "https://cloudresourcemanager.googleapis.com", "HTTP_JSON"),
    "iamcredentials": ("baseline", "https://iamcredentials.googleapis.com", "HTTP_JSON"),
    "aiplatform": ("callers", "https://aiplatform.googleapis.com", "HTTP_JSON"),
    "bigquery": ("callers", "https://bigquery.googleapis.com", "HTTP_JSON"),
    "bigquerystorage": ("callers", "https://bigquerystorage.googleapis.com", "GRPC"),
    "storage": ("callers", "https://storage.googleapis.com", "HTTP_JSON"),
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
    # The legacy map, plus each entry's Agent Registry description (#220).
    assert all(entry["description"] for entry in compiled.values())
    assert {n: {k: v for k, v in e.items() if k != "description"} for n, e in compiled.items()} == ENV_MAPS[env]
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
        'probe = "query", description = "' in hcl
    )
    assert 'timeout = 600 }' in hcl
    assert 'callers = ["cx-mortgage-orchestrator", "test-vm"]' in hcl


# ------------------------------------------------------------------------------------------
# Agent Gateway per-target lists (iap_egress)
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_repo_catalog_compiles_the_former_hand_written_iap_lists(env):
    """Same members, same order as the lists the iap-egress units used to hand-write."""
    resolved = catalog.resolve(REPO, env)
    assert catalog.compile_iap_egress(resolved) == ENV_IAP_EGRESS[env]
    data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
    assert data["iap_egress"] == ENV_IAP_EGRESS[env]


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
    # Agent callers first (their Agent Identity principal), then the rest.
    assert catalog.compile_iap_egress(resolved)["mcp_servers"]["legacy-dms"] == [
        "agents/ai-coe-mortgage-specialist:agent_identity_principal",
        "agents/cx-mortgage-orchestrator:agent_identity_principal",
        "layer-3-security:test_vm_sa_email",
    ]
    # Other targets are untouched.
    assert catalog.compile_iap_egress(resolved)["mcp_servers"]["corporate-email"] == _SPECIALIST_ON_MCP


_SHARED_POLICY = """  agent_gateway:
    shared_workload_identity:
      ref: "layer-3-security:mcp_invoker_sa_email"
      kinds: [mcp]
"""


def _with_shared_identity(repo: Path, kinds: str = "[mcp]") -> None:
    """The (deprecated) policy block that lists one shared outbound identity on agent-called targets."""
    path = repo / "catalog/policy.yaml"
    text = path.read_text()
    assert "  agent_gateway:\n" in text
    path.write_text(text.replace("  agent_gateway:\n", _SHARED_POLICY.replace("[mcp]", kinds), 1))


def test_repo_policy_has_no_shared_workload_identity():
    """Agents mint their own tokens (#134): nothing adds a shared identity to the gateway lists."""
    resolved = catalog.resolve(REPO, "dev")
    assert "shared_workload_identity" not in ((resolved.policy.get("agent_gateway") or {}))
    for env in ("dev", "prd"):
        data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
        assert "mcp_invoker" not in json.dumps(data)
        assert data["attribution"]["shared_principals"] == []


def test_shared_workload_identity_comes_from_the_policy(repo):
    _with_shared_identity(repo)
    iap = catalog.compile_iap_egress(catalog.resolve(repo, "dev"))
    assert iap["mcp_servers"]["legacy-dms"] == [
        "agents/ai-coe-mortgage-specialist:agent_identity_principal",
        "layer-3-security:mcp_invoker_sa_email",
        "layer-3-security:test_vm_sa_email",
    ]
    # Only added where an agent calls: a target with only non-agent callers never gets it.
    _write(repo, "environments/dev.yaml",
           "environment: dev\noverrides:\n  targets:\n    legacy-dms:\n"
           "      callers: { ai-coe-mortgage-specialist: null }\n")
    iap = catalog.compile_iap_egress(catalog.resolve(repo, "dev"))
    assert iap["mcp_servers"]["legacy-dms"] == ["layer-3-security:test_vm_sa_email"]


def test_shared_workload_identity_kinds_are_honoured(repo):
    _with_shared_identity(repo, "[mcp, a2a]")
    iap = catalog.compile_iap_egress(catalog.resolve(repo, "dev"))
    assert iap["agents"]["ai-coe-mortgage-specialist"] == [
        "agents/cx-mortgage-orchestrator:agent_identity_principal",
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
    assert set(iap) == {"agents", "endpoints", "mcp_servers"}
    # adk agents (the orchestrator), examples and models on Google APIs have no Agent Registry entry
    # of their own.
    assert "cx-mortgage-orchestrator" not in iap["agents"]
    for group in iap.values():
        assert "example-core-banking" not in group and "gemini-flash" not in group


# ------------------------------------------------------------------------------------------
# Google API egress (google_apis): declared in the catalog, least privilege per agent
# ------------------------------------------------------------------------------------------

_ORCH_FILE = "catalog/teams/cx/agents/cx-mortgage-orchestrator.yaml"
_SPEC_FILE = "catalog/teams/ai-coe/agents/ai-coe-mortgage-specialist.yaml"


def _edit(repo: Path, rel: str, old: str, new: str) -> None:
    path = repo / rel
    text = path.read_text()
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new))


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_repo_catalog_compiles_the_least_privilege_google_apis(env):
    google = catalog.compile_google_apis(catalog.resolve(REPO, env))
    assert {sid: (e["grant"], e["url"], e["protocol_binding"]) for sid, e in google.items()} == GOOGLE_API_ENTRIES
    both = ["ai-coe-mortgage-specialist", "cx-mortgage-orchestrator"]
    for sid, entry in google.items():
        expected = both if entry["grant"] == "baseline" or sid == "aiplatform" else ["ai-coe-mortgage-specialist"]
        assert entry["callers"] == expected, sid
    data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
    assert data["google_apis"] == google


def test_no_agent_reaches_a_google_api_it_doesnt_declare():
    """Per-agent Google API entries list exactly the declaring agents; baseline ones are layer 4's."""
    resolved = catalog.resolve(REPO, "dev")
    google = catalog.compile_google_apis(resolved)
    endpoints = catalog.compile_iap_egress(resolved)["endpoints"]
    baseline = set(catalog.resolver.google_api_baseline(resolved.policy))
    for name in ("cx-mortgage-orchestrator", "ai-coe-mortgage-specialist"):
        principal = f"agents/{name}:agent_identity_principal"
        declared = set(resolved.targets[name].get("google_apis") or []) - baseline
        reachable = {google[sid]["api"] for sid, members in endpoints.items() if sid in google and principal in members}
        assert reachable == declared, name
    for sid, entry in google.items():
        assert (sid in endpoints) == (entry["grant"] == "callers"), sid
    # The orchestrator never reaches BigQuery or Cloud Storage.
    for sid in ("bigquery", "bigquerystorage", "storage"):
        assert endpoints[sid] == _SPECIALIST


def test_an_unknown_google_api_fails(repo):
    _edit(repo, _ORCH_FILE, "  - aiplatform  #", "  - pubsub\n  - aiplatform  #")
    assert "unknown Google API 'pubsub' in google_apis" in _problems(repo)


def test_an_unknown_baseline_api_fails(repo):
    _edit(repo, "catalog/policy.yaml", "        - telemetry ", "        - modelarmor_rep\n        - telemetry ")
    assert "policy.agent_gateway.google_apis.baseline: unknown Google API 'modelarmor_rep'" in _problems(repo)


def test_google_apis_are_for_agents_only(repo):
    _edit(repo, "catalog/teams/ai-coe/mcp/legacy-dms.yaml", "\ncallers:", "\ngoogle_apis: [storage]\ncallers:")
    assert "google_apis is only for agents" in _problems(repo)


def test_an_agent_must_declare_the_google_api_of_its_model(repo):
    _edit(repo, _ORCH_FILE, "google_apis:\n  - aiplatform  # Gemini generateContent (MODEL_LOCATION=global)\n", "")
    problems = _problems(repo)
    assert "calls model 'gemini-flash' on the Google API 'aiplatform' but doesn't declare it" in problems


def test_a_model_on_an_unknown_google_host_fails(repo):
    _edit(repo, "catalog/teams/platform/models/gemini-flash.yaml", "https://aiplatform.googleapis.com",
          "https://us-aiplatform.googleapis.com")
    assert "deploy.endpoint host 'us-aiplatform.googleapis.com' is not a known Google API host" in _problems(repo)


def test_a_baseline_api_an_agent_also_declares_stays_a_baseline_entry(repo):
    _edit(repo, _SPEC_FILE, "  - storage ", "  - telemetry\n  - storage ")
    resolved = catalog.resolve(repo, "dev")
    assert catalog.compile_google_apis(resolved)["telemetry"]["grant"] == "baseline"
    assert "telemetry" not in catalog.compile_iap_egress(resolved)["endpoints"]


def test_an_api_no_agent_declares_is_not_registered(repo):
    _edit(repo, _SPEC_FILE, "  - storage          # the same plugin's offload of large or multimodal content "
                            "(GCS_BUCKET)\n", "")
    resolved = catalog.resolve(repo, "dev")
    assert "storage" not in catalog.compile_google_apis(resolved)
    assert "storage" not in catalog.compile_iap_egress(resolved)["endpoints"]


def test_a_target_may_not_take_a_google_api_entry_name(repo):
    _write(repo, "external/legacy/storage.yaml",
           "name: storage\nkind: http\nowner_team: platform\n"
           "deploy: { managed: false, endpoint: \"https://storage.example.com\" }\n")
    assert "the name 'storage' is the Agent Registry entry of the Google API 'storage'" in _problems(repo)


def test_every_known_google_api_is_a_plain_googleapis_host():
    for name, api in catalog.GOOGLE_APIS.items():
        host = api.host.replace("{region}", "us-central1")
        assert host.endswith(".googleapis.com") and ".mtls." not in host and ".rep." not in host, name
        assert not host.startswith("us-") or name == "aiplatform_runtime_region", name
        assert api.protocol_binding in ("HTTP_JSON", "GRPC"), name
    assert catalog.GOOGLE_APIS["bigquerystorage"].protocol_binding == "GRPC"


def test_registry_descriptions_say_why_and_who():
    """Every compiled Agent Registry entry: its catalog text, then the callers (#220)."""
    resolved = catalog.resolve(REPO, "dev")
    google = catalog.compile_google_apis(resolved)
    assert google["bigquery"]["description"] == (
        catalog.GOOGLE_APIS["bigquery"].description + " Used by: ai-coe-mortgage-specialist."
    )
    assert google["telemetry"]["description"].endswith(
        " Used by every agent (baseline): ai-coe-mortgage-specialist, cx-mortgage-orchestrator."
    )
    external = catalog.compile_external_targets(resolved)["external-probe"]
    assert external["description"].startswith("Synthetic test target with no backend.")
    assert external["description"].endswith(" Used by: ai-coe-mortgage-specialist.")
    compiled = catalog.compile_terraform(resolved)
    assert compiled["legacy-dms"]["description"].endswith(" Used by: ai-coe-mortgage-specialist, test-vm.")
    assert compiled["ai-coe-mortgage-specialist"]["description"].endswith(
        " Used by: cx-mortgage-orchestrator, test-vm."
    )


def test_registry_description_format_and_limit():
    describe = catalog.resolver.registry_description
    assert describe("Why  it\n exists", ["a", "b"]) == "Why it exists. Used by: a, b."
    assert describe("Why.", []) == "Why. Used by: no caller declared."
    assert describe("Why.", ["a"], baseline=True) == "Why. Used by every agent (baseline): a."
    with pytest.raises(catalog.CatalogError, match="over the 2048 limit"):
        describe("x" * 2048, ["a"])


def test_a_target_with_a_registry_entry_needs_a_description(repo):
    _edit(repo, "catalog/teams/ai-coe/mcp/legacy-dms.yaml",
          "description: MCP server on Cloud Run over the legacy document management system (synthetic data).\n", "")
    assert "target 'legacy-dms'" in _problems(repo)
    assert "description is required" in _problems(repo)


def test_show_lists_the_google_apis_of_each_agent():
    resolved = catalog.resolve(REPO, "dev")
    (row,) = catalog.show(resolved, target="cx-mortgage-orchestrator")["targets"]
    assert row["google_apis"] == [
        "telemetry", "aiplatform_runtime_region", "cloudresourcemanager", "iamcredentials", "aiplatform",
    ]
    (model,) = catalog.show(resolved, target="gemini-flash")["targets"]
    assert model["registry"].startswith("Google API entry")


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
    dev = catalog.compile_terraform(catalog.resolve(repo, "dev"))
    assert {n: {k: v for k, v in e.items() if k != "description"} for n, e in dev.items()} == DEV_MAP


def test_override_is_validated(repo):
    _write(repo, "environments/dev.yaml",
           "environment: dev\noverrides:\n  targets:\n    legacy-dms:\n      callers: { ghost: {} }\n")
    assert "unknown caller 'ghost'" in _problems(repo)


def test_enabled_network_source_without_its_snapshot_names_the_fetch_command(repo):
    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("type: agent_registry\n    enabled: false", "type: agent_registry"))
    assert "esmeralda catalog fetch --env dev --source agent-registry" in _problems(repo)


def test_registered_adapter_never_merges_into_a_target_by_name(repo, monkeypatch):
    """An adapter can't fill fields of a catalog target it names: adoption needs a deploy.registry pin
    (the multi-source rules are covered in test_catalog_sources.py)."""
    from esmeralda.catalog import Layer, Source, register_source
    from esmeralda.catalog import sources as catalog_sources

    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("test_inventory")
    class Inventory(Source):
        CAPABILITIES = {"inventory": ["*"], "targets": ["description"]}

        def load(self) -> Layer:
            layer = Layer(source=self.name)
            layer.targets["legacy-dms"] = {"description": "from inventory"}
            return layer

    path = repo / "catalog/sources.yaml"
    text = path.read_text().replace(
        "  # Discovered inventory from Agent Registry",
        "  - name: inventory\n    type: test_inventory\n\n  # Discovered inventory from Agent Registry",
    )
    assert "type: test_inventory" in text
    path.write_text(text)
    assert "inventory: may not set targets.legacy-dms: " in _problems(repo)
    assert "never by name" in _problems(repo)


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
    assert "legacy-dms" in out and "platform / cc-platform" in out


def test_cli_reports_catalog_errors(capsys):
    assert cli.main(["catalog", "show", "--env", "stg", "--repo", str(REPO)]) == 2
    assert "unknown environment 'stg'" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------
# verified attribution (principal_teams, caller allowlists, policy)
# ------------------------------------------------------------------------------------------


def test_principal_teams_maps_every_verified_identity_to_its_team():
    rows = {r["principal"]: r for r in catalog.compile_principal_teams(catalog.resolve(REPO, "dev"))}
    assert rows["agents/cx-mortgage-orchestrator:agent_identity_principal"] == {
        "principal": "agents/cx-mortgage-orchestrator:agent_identity_principal", "principal_type": "agent_runtime",
        "caller": "cx-mortgage-orchestrator", "team": "cx", "cost_centers": ["cc-cx-br", "cc-cx-mx"],
    }
    assert rows["agents/ai-coe-mortgage-specialist:agent_identity_principal"]["team"] == "ai-coe"
    assert rows["layer-3-security:test_vm_sa_email"]["principal_type"] == "service_account"
    assert rows["group:platform-ops@example.com"] == {
        "principal": "group:platform-ops@example.com", "principal_type": "group", "caller": "platform-ops",
        "team": "platform", "cost_centers": ["cc-platform"],
    }
    # Agents call out with their own Agent Identity: no shared invoker, no agent service account rows.
    assert "layer-3-security:mcp_invoker_sa_email" not in rows
    assert "layer-3-security:ai_coe_mortgage_specialist_sa_email" not in rows
    assert "layer-3-security:cx_mortgage_orchestrator_sa_email" not in rows
    # Examples never become attribution rows.
    assert not any(r["caller"] == "aws-underwriter" for r in rows.values())
    # A unit-output identity no target of the environment lists is left out (it may not exist there).
    assert "layer-5-workloads/apigee/platform:proxy_runtime_sa_email" in rows
    prd = {r["principal"] for r in catalog.compile_principal_teams(catalog.resolve(REPO, "prd"))}
    assert "layer-5-workloads/apigee/platform:proxy_runtime_sa_email" not in prd
    assert "group:platform-ops@example.com" in prd


def test_attribution_block_is_in_the_committed_resolved_files():
    for env in ("dev", "prd"):
        data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
        resolved = catalog.resolve(REPO, env)
        assert data["attribution"] == catalog.compile_attribution(resolved)
        assert data["attribution"]["shared_principals"] == []
    dev = json.loads((REPO / "catalog/.resolved/dev.json").read_text())["attribution"]
    prd = json.loads((REPO / "catalog/.resolved/prd.json").read_text())["attribution"]
    # Rollout done: dev flagged unknown callers first and now denies, like prd.
    assert dev["policy"] == {"unmapped_caller": "deny", "declared_mismatch": "reject"}
    assert prd["policy"] == {"unmapped_caller": "deny", "declared_mismatch": "reject"}


def test_caller_allowlists_follow_the_iam_callers():
    resolved = catalog.resolve(REPO, "dev")
    allowlists = catalog.compile_caller_allowlists(resolved)
    assert set(allowlists) == {"legacy-dms", "income-verification", "corporate-email", "ai-coe-mortgage-specialist"}
    by_principal = {e["principal"]: e for e in allowlists["corporate-email"]}
    assert by_principal["layer-5-workloads/apigee/platform:proxy_runtime_sa_email"]["caller"] == "apigee-partner-mcp"
    # Agent callers are allowlisted by their Agent Identity principal (the guard maps spiffe:// to it).
    assert by_principal["agents/ai-coe-mortgage-specialist:agent_identity_principal"] == {
        "principal": "agents/ai-coe-mortgage-specialist:agent_identity_principal",
        "caller": "ai-coe-mortgage-specialist", "team": "ai-coe", "cost_centers": ["cc-ai-coe"],
    }
    assert not any(e.get("shared") for entries in allowlists.values() for e in entries)
    # The orchestrator's grant limits the cost centres it may declare on the specialist.
    spec = {e["principal"]: e for e in allowlists["ai-coe-mortgage-specialist"]}
    assert spec["agents/cx-mortgage-orchestrator:agent_identity_principal"]["cost_centers"] == ["cc-cx-br", "cc-cx-mx"]
    # Groups get no IAM grant, so no allowlist entry; the ADK orchestrator has no guard.
    assert "cx-mortgage-orchestrator" not in allowlists


def test_shared_identity_across_teams_is_verified_without_a_team(repo):
    _with_shared_identity(repo)
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
    # Unreferenced unit-output identities get no row; once a target lists it, the conflict shows.
    catalog.compile_principal_teams(catalog.resolve(repo, "dev"))
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    path.write_text(path.read_text().replace("  test-vm: {}\n", "  test-vm: {}\n  cx-batch: {}\n"))
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


# ------------------------------------------------------------------------------------------
# managed: false targets: Agent Registry entries and per-target gateway lists
# ------------------------------------------------------------------------------------------

def _external(root: Path, name: str, body: str) -> None:
    _write(root, f"external/legacy/{name}.yaml", f"name: {name}\n" + body)


_HTTP = ("kind: http\nowner_team: platform\ndescription: Synthetic legacy API.\n"
         "deploy: {{ managed: false, endpoint: \"{endpoint}\" }}\n")


def test_dev_external_targets_are_registered_with_their_callers_only():
    resolved = catalog.resolve(REPO, "dev")
    assert catalog.compile_external_targets(resolved) == {
        "external-probe": {
            "kind": "http",
            "registry": "endpoint",
            "url": "https://external-probe.{domain}",
            "protocol_binding": "HTTP_JSON",
            "description": " ".join(resolved.targets["external-probe"]["description"].split())
            + " Used by: ai-coe-mortgage-specialist.",
            "owner_team": "platform",
        },
        "llm-gateway": {
            "kind": "http",
            "registry": "endpoint",
            "url": "https://apigee.{domain}/llm",
            "protocol_binding": "HTTP_JSON",
            "description": " ".join(resolved.targets["llm-gateway"]["description"].split())
            + " Used by: cx-mortgage-orchestrator, ai-coe-mortgage-specialist.",
            "owner_team": "platform",
        },
    }
    assert catalog.compile_iap_egress(resolved)["endpoints"] == {
        **GOOGLE_API_IAP,
        "external-probe": _SPECIALIST,
        "llm-gateway": _AGENTS,
    }
    # Not deployed by a unit: no entry point route, no IAM grant, no in-process allowlist.
    for name in ("external-probe", "llm-gateway"):
        assert name not in catalog.compile_terraform(resolved)
        assert name not in catalog.compile_caller_allowlists(resolved)
    # prd has none (its endpoints are the per-agent Google API entries only).
    prd = catalog.resolve(REPO, "prd")
    assert catalog.compile_external_targets(prd) == {} and catalog.compile_iap_egress(prd)["endpoints"] == GOOGLE_API_IAP


def test_external_targets_are_in_the_committed_resolved_files():
    for env in ("dev", "prd"):
        data = json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())
        assert data["external_targets"] == catalog.compile_external_targets(catalog.resolve(REPO, env))


def test_examples_and_google_api_hosts_are_never_registered():
    resolved = catalog.resolve(REPO, "dev")
    external = catalog.compile_external_targets(resolved)
    assert "example-core-banking" not in external  # example: true wins
    assert "gemini-flash" not in external  # aiplatform.googleapis.com: a layer-4 Google API entry
    rows = {r["target"]: r for r in catalog.show(resolved)["targets"]}
    assert rows["gemini-flash"]["registry"].startswith("Google API entry")
    # Enforced: both callers are deployed agents that declare aiplatform (google_apis).
    assert all(c["enforced"] for c in rows["gemini-flash"]["callers"])
    assert rows["external-probe"]["registry"] == "endpoints (per-target caller list)"
    assert all(c["enforced"] for c in rows["external-probe"]["callers"])


@pytest.mark.parametrize(
    ("kind", "registry", "group", "binding"),
    [
        ("http", "endpoint", "endpoints", "HTTP_JSON"),
        ("model", "endpoint", "endpoints", "HTTP_JSON"),
        ("mcp", "mcp_server", "mcp_servers", "JSONRPC"),
        ("a2a", "agent", "agents", "HTTP+JSON"),
    ],
)
def test_each_kind_maps_to_its_registry_entry(repo, kind, registry, group, binding):
    _external(repo, "brownfield", f"kind: {kind}\nowner_team: platform\ndescription: Brownfield {kind} target.\n"
              "deploy: { managed: false, endpoint: \"https://brownfield.example.com/api\" }\n"
              "callers:\n  ai-coe-mortgage-specialist: {}\n  test-vm: {}\n")
    resolved = catalog.resolve(repo, "prd")
    entry = catalog.compile_external_targets(resolved)["brownfield"]
    assert (entry["registry"], entry["protocol_binding"], entry["url"]) == (
        registry, binding, "https://brownfield.example.com/api")
    members = catalog.compile_iap_egress(resolved)[group]["brownfield"]
    assert members == [*_SPECIALIST, "layer-3-security:test_vm_sa_email"]
    # No shared identity: agents call out as their own Agent Identity, whatever the kind.
    assert "layer-3-security:mcp_invoker_sa_email" not in members


def test_external_target_with_no_callers_grants_nothing(repo):
    """Guard: a managed: false target with no (enforced) callers is listed with an empty list."""
    _external(repo, "lonely-api", _HTTP.format(endpoint="https://lonely.example.com"))
    _external(repo, "groups-only", _HTTP.format(endpoint="https://groups.example.com")
              + "callers:\n  platform-ops: {}\n")
    resolved = catalog.resolve(repo, "prd")
    endpoints = catalog.compile_iap_egress(resolved)["endpoints"]
    assert endpoints == {**GOOGLE_API_IAP, "groups-only": [], "lonely-api": []}
    assert set(catalog.compile_external_targets(resolved)) == {"groups-only", "lonely-api"}
    # And nothing about them reaches the IAM / allowlist side.
    assert not {"groups-only", "lonely-api"} & set(catalog.compile_terraform(resolved))


def test_removing_an_external_target_revokes_its_entry_and_list(repo):
    _external(repo, "legacy-crm", _HTTP.format(endpoint="https://crm.example.com")
              + "callers:\n  ai-coe-mortgage-specialist: {}\n")
    assert "legacy-crm" in catalog.compile_iap_egress(catalog.resolve(repo, "prd"))["endpoints"]
    (repo / "catalog/external/legacy/legacy-crm.yaml").unlink()
    resolved = catalog.resolve(repo, "prd")
    assert "legacy-crm" not in catalog.compile_external_targets(resolved)
    assert "legacy-crm" not in catalog.compile_iap_egress(resolved)["endpoints"]


def test_only_listed_callers_are_on_an_external_list(repo):
    _external(repo, "legacy-crm", _HTTP.format(endpoint="https://crm.example.com")
              + "callers:\n  ai-coe-mortgage-specialist: {}\n")
    members = catalog.compile_iap_egress(catalog.resolve(repo, "prd"))["endpoints"]["legacy-crm"]
    assert not any("cx-mortgage-orchestrator" in m or "cx_mortgage_orchestrator" in m for m in members)


def test_external_callers_are_attributable(repo):
    """allowed => attributable covers the external lists (#186): every member has a principal_teams row."""
    _write(repo, "teams/platform/batch.yaml",
           "callers:\n  batch-job:\n    team: platform\n"
           "    identity: { type: service_account, id: batch-job@example.iam.gserviceaccount.com }\n")
    _external(repo, "legacy-crm", _HTTP.format(endpoint="https://crm.example.com")
              + "callers:\n  batch-job: {}\n  ai-coe-mortgage-specialist: {}\n")
    resolved = catalog.resolve(repo, "prd")
    rows = {r["principal"]: r for r in catalog.compile_attribution(resolved)["principal_teams"]}
    for member in catalog.compile_iap_egress(resolved)["endpoints"]["legacy-crm"]:
        assert member in rows, member
    assert rows["batch-job@example.iam.gserviceaccount.com"]["team"] == "platform"


def test_direct_callers_are_never_on_an_external_list(repo):
    _external(repo, "legacy-crm", _HTTP.format(endpoint="https://crm.example.com")
              + "callers:\n  apigee-partner-mcp: {}\n")
    assert catalog.compile_iap_egress(catalog.resolve(repo, "prd"))["endpoints"]["legacy-crm"] == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_HTTP.format(endpoint="https://x.{tenant}.example.com"), "unknown endpoint placeholder(s) {tenant}"),
        (_HTTP.format(endpoint="https://a.b.{domain}"), "must be one label under {domain}"),
        (_HTTP.format(endpoint="https://*.example.com"), "must be an exact hostname"),
        (_HTTP.format(endpoint="https://Upper.example.com"), "must be an exact hostname"),
        (_HTTP.format(endpoint="https://example.com?x=1"), "is not https://<host>[:port][/path]"),
        ("kind: adk\nowner_team: platform\ndeploy: { managed: false, endpoint: \"https://a.example.com\" }\n",
         "declare an agent running elsewhere as kind a2a"),
    ],
    ids=["unknown-placeholder", "nested-domain", "wildcard", "uppercase", "query", "adk"],
)
def test_bad_external_endpoints_fail(repo, body, message):
    _external(repo, "bad-target", body)
    assert message in _problems(repo, "prd")


def test_external_hosts_must_be_unique_and_not_shadow_entry_point_hosts(repo):
    _external(repo, "crm-a", _HTTP.format(endpoint="https://crm.example.com/a"))
    _external(repo, "crm-b", _HTTP.format(endpoint="https://crm.example.com/b"))
    _external(repo, "fake-dms", _HTTP.format(endpoint="https://legacy-dms.{domain}"))
    problems = _problems(repo, "prd")
    assert "endpoint host 'crm.example.com' is also the host of target 'crm-a'" in problems
    assert "is the entry point host of deployed target 'legacy-dms'" in problems


def test_external_gateway_caller_with_an_unmapped_ref_fails(repo):
    path = repo / "catalog/teams/platform/callers.yaml"
    path.write_text(path.read_text().replace("    agent_gateway: false\n", ""))
    _write(repo, "environments/prd.yaml", "environment: prd\n")
    _external(repo, "legacy-crm", _HTTP.format(endpoint="https://crm.example.com")
              + "callers:\n  apigee-partner-mcp: {}\n")
    assert "caller 'apigee-partner-mcp' would go on the Agent Gateway list" in _problems(repo, "prd")


def test_model_gateway_declaration_compiles(repo):
    """The shape the Apigee model gateway will use: kind http on apigee.{domain}, agents as callers."""
    _write(repo, "teams/platform/gateways/llm-gateway.yaml",
           "name: llm-gateway\nkind: http\nowner_team: platform\n"
           "description: Model gateway proxy (agent -> model).\n"
           "deploy: { managed: false, endpoint: \"https://apigee.{domain}/llm\" }\n"
           "serving: { token: id }\ngateway: apigee\n"
           "callers:\n  ai-coe-mortgage-specialist: { cost_centers: [cc-ai-coe] }\n"
           "  cx-mortgage-orchestrator: { cost_centers: [cc-cx-br, cc-cx-mx] }\n")
    resolved = catalog.resolve(repo, "prd")
    assert catalog.compile_external_targets(resolved)["llm-gateway"]["url"] == "https://apigee.{domain}/llm"
    assert catalog.compile_iap_egress(resolved)["endpoints"]["llm-gateway"] == [
        *_SPECIALIST,
        "agents/cx-mortgage-orchestrator:agent_identity_principal",
    ]
