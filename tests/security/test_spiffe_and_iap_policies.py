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

"""Unit and contract tests for GEAP SPIFFE Agent Identity & per-target IAP/UAP policies."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGW_MAIN_TF = REPO_ROOT / "infrastructure/modules/4-governance/modules/6_agent_gateway/main.tf"
SECURITY_AGENT_ID_TF = REPO_ROOT / "infrastructure/modules/3-security/agent_identity.tf"
ADK_AGENT_DIR = REPO_ROOT / "infrastructure/modules/5-workloads/agents/adk-agent"
A2A_AGENT_DIR = REPO_ROOT / "infrastructure/modules/5-workloads/agents/a2a-agent"
IAP_EGRESS_MOD_DIR = REPO_ROOT / "infrastructure/modules/5-workloads/iap-egress"
GRANT_IAP_SCRIPT = REPO_ROOT / "infrastructure/modules/_shared/scripts/grant_iap_egress.sh"
DEV_IAP_TG = REPO_ROOT / "infrastructure/live/dev/layer-5-workloads/iap-egress/terragrunt.hcl"
PRD_IAP_TG = REPO_ROOT / "infrastructure/live/prd/layer-5-workloads/iap-egress/terragrunt.hcl"

GOVERNANCE_PROJECT_NUMBER = "000000000000"
REGION = "us-central1"
ENDPOINTS_PREFIX = f"projects/{GOVERNANCE_PROJECT_NUMBER}/locations/{REGION}/iap_web/agentRegistry/endpoints/"
ROOT_CONDITION_EXPRESSION = f'resource.name.startsWith("{ENDPOINTS_PREFIX}")'


def test_agent_gateway_eliminates_org_wide_wildcard_principal_set():
    """6_agent_gateway/main.tf must never grant roles/iap.egressor to org-wide system.id.goog/*."""
    content = AGW_MAIN_TF.read_text()
    assert "system.id.goog/*" not in content, (
        "6_agent_gateway/main.tf must not contain the org-wide wildcard principalSet (*.system.id.goog/*)"
    )
    assert "attribute.platformContainer/aiplatform/projects/" in content
    assert "attribute.container/projects/" in content


@pytest.mark.parametrize("agent_dir", [ADK_AGENT_DIR, A2A_AGENT_DIR], ids=["adk-agent", "a2a-agent"])
def test_agent_modules_export_spiffe_and_principal_outputs(agent_dir: pathlib.Path):
    """Both adk-agent and a2a-agent must export agent_identity_principal (principal://) and spiffe_id (spiffe://)."""
    main_tf = (agent_dir / "main.tf").read_text()
    outputs_tf = (agent_dir / "outputs.tf").read_text()
    variables_tf = (agent_dir / "variables.tf").read_text()

    # Outputs
    assert 'output "agent_identity_principal"' in outputs_tf
    assert 'output "spiffe_id"' in outputs_tf

    # Canonical SPIFFE / principal construction
    assert "spiffe://${local.trust_domain}/${local.spiffe_resource_path}" in main_tf
    assert "principal://${local.trust_domain}/${local.spiffe_resource_path}" in main_tf
    assert "resources/aiplatform/projects/${data.google_project.current.number}/locations/${var.region}/reasoningEngines/" in main_tf

    # Direct principal:// resource bindings when enable_agent_identity = true
    # (BigQuery writer is a project-level grant since the central agent analytics change.)
    assert 'resource "google_project_iam_member" "agent_identity_bq_writer"' in main_tf
    assert 'resource "google_storage_bucket_iam_member" "agent_identity_storage_user"' in main_tf
    assert "member     = local.agent_identity_principal" in main_tf or "member = local.agent_identity_principal" in main_tf

    # Native SPIFFE auth toggle omits SERVICE_ACCOUNT_EMAIL when enabled
    assert 'variable "enable_native_spiffe_auth"' in variables_tf
    assert "var.enable_native_spiffe_auth ? null" in main_tf


def test_layer_3_security_restricts_orchestrator_from_mcp_project_egress():
    """Layer 3 security must not grant cx-mortgage-orchestrator roles/iap.egressor on mcps_project_id."""
    content = SECURITY_AGENT_ID_TF.read_text()
    assert 'resource "google_project_iam_member" "ai_coe_mortgage_specialist_identity_mcp_run_invoker"' in content

    # Extract the cx_mortgage_orchestrator_iap_egressor resource block
    marker = 'resource "google_project_iam_member" "cx_mortgage_orchestrator_iap_egressor"'
    assert marker in content
    block = content.split(marker, 1)[1].split("\n}", 1)[0]
    assert "var.ai_coe_agents_project_id" in block
    assert "var.mcps_project_id" not in block, (
        "cx_mortgage_orchestrator_iap_egressor must not include var.mcps_project_id"
    )


def _hcl_list_block(content: str, key: str) -> str:
    """Return the body of the HCL list assigned to `"<key>" = [ ... ]`."""
    marker = f'"{key}" = ['
    assert marker in content, f"missing {marker}"
    return content.split(marker, 1)[1].split("]", 1)[0]


@pytest.mark.parametrize("tg_path", [DEV_IAP_TG, PRD_IAP_TG], ids=["dev", "prd"])
def test_iap_egress_terragrunt_enforces_least_privilege_per_target_callers(tg_path: pathlib.Path):
    """iap-egress terragrunt units must separate specialist callers (orchestrator) from MCP callers (specialist)."""
    content = tg_path.read_text()

    assert "target_iap_members" in content
    # Root members are the same list layer 4 writes, so both writers produce the same root policy.
    assert "iap_egress_members = dependency.governance.outputs.iap_egress_members" in content

    # Specialist callers: orchestrator (+ test VM), never the specialist itself
    spec_block = _hcl_list_block(content, "ai-coe-mortgage-specialist")
    assert "dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal" in spec_block
    assert "cx_mortgage_orchestrator_sa_email" in spec_block
    assert "ai_coe_mortgage_specialist" not in spec_block

    # MCP callers: specialist (+ invoker SA, test VM), never the orchestrator
    for mcp_id in ("corporate-email", "income-verification", "legacy-dms"):
        mcp_block = _hcl_list_block(content, mcp_id)
        assert "dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal" in mcp_block
        assert "ai_coe_mortgage_specialist_sa_email" in mcp_block
        assert "cx_mortgage_orchestrator" not in mcp_block


@pytest.mark.parametrize(
    "tf_path",
    [AGW_MAIN_TF, IAP_EGRESS_MOD_DIR / "main.tf"],
    ids=["layer4-agent-gateway", "layer5-iap-egress"],
)
def test_both_root_policy_writers_pass_governance_project_number(tf_path: pathlib.Path):
    """Layer 4 and layer 5 both run grant_iap_egress.sh and must pass the inputs of the conditional root policy."""
    content = tf_path.read_text()
    assert "grant_iap_egress.sh" in content
    assert "GOVERNANCE_PROJECT_NUMBER = data.google_project.governance.number" in content
    assert "IAP_MEMBERS_JSON" in content
    # The script path must resolve from the module directory to modules/_shared/scripts.
    rel = content.split('command = "bash ${path.module}/', 1)[1].split('"', 1)[0]
    assert (tf_path.parent / rel).resolve() == GRANT_IAP_SCRIPT.resolve()


def test_layer4_root_iap_members_exclude_service_account_emails():
    """The registry-root IAP members are agent principals, not the agent/test VM SA emails (CA secret readers)."""
    content = AGW_MAIN_TF.read_text()
    locals_block = content.split("dynamic_agent_members = flatten([", 1)[1].split("\n}", 1)[0]
    assert "var.agent_invoker_sa_emails" not in locals_block


def _run_grant_script_with_mock_gcloud(
    tmp_path: pathlib.Path,
    *,
    iap_members: list[str],
    target_policies: dict | None = None,
    extra_env: dict[str, str] | None = None,
    check: bool = True,
) -> tuple[subprocess.CompletedProcess, list[dict]]:
    """Run grant_iap_egress.sh with a fake gcloud that records each set-iam-policy call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    calls_log = tmp_path / "gcloud_calls.jsonl"

    fake_gcloud = bin_dir / "gcloud"
    fake_gcloud.write_text(
        f"""#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
if args[:3] == ["iap", "web", "set-iam-policy"]:
    policy_path = args[3]
    policy = json.loads(pathlib.Path(policy_path).read_text())
    entry = {{"args": args[4:], "policy": policy}}
    with open({str(calls_log)!r}, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\\n")
sys.exit(0)
"""
    )
    fake_gcloud.chmod(fake_gcloud.stat().st_mode | stat.S_IEXEC)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "GOVERNANCE_PROJECT_ID": "esm-dev-governance",
        "GOVERNANCE_PROJECT_NUMBER": GOVERNANCE_PROJECT_NUMBER,
        "REGION": REGION,
        "IAP_MEMBERS_JSON": json.dumps(iap_members),
    }
    if target_policies is not None:
        env["IAP_TARGET_POLICIES_JSON"] = json.dumps(target_policies)
    else:
        env.pop("IAP_TARGET_POLICIES_JSON", None)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    res = subprocess.run(
        ["bash", str(GRANT_IAP_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )
    calls = (
        [json.loads(line) for line in calls_log.read_text().splitlines() if line.strip()]
        if calls_log.exists()
        else []
    )
    return res, calls


def _is_root_call(call: dict) -> bool:
    return not any(a.startswith("--mcp-server=") or a.startswith("--agent=") for a in call["args"])


def _assert_conditional_root_policy(policy: dict, members: list[str]) -> None:
    assert policy["version"] == 3
    assert policy["bindings"] == [
        {
            "role": "roles/iap.egressor",
            "members": members,
            "condition": {
                "title": "system-endpoints-only",
                "description": policy["bindings"][0]["condition"]["description"],
                "expression": ROOT_CONDITION_EXPRESSION,
            },
        }
    ]
    assert policy["bindings"][0]["condition"]["description"]


def test_grant_iap_egress_script_per_target_policies(tmp_path: pathlib.Path):
    """When IAP_TARGET_POLICIES_JSON is set, grant_iap_egress.sh applies distinct per-MCP and per-agent policies."""
    orch_principal = (
        "principal://agents.global.org-123456.system.id.goog/"
        "resources/aiplatform/projects/111/locations/us-central1/reasoningEngines/orch-1"
    )
    spec_principal = (
        "principal://agents.global.org-123456.system.id.goog/"
        "resources/aiplatform/projects/222/locations/us-central1/reasoningEngines/spec-1"
    )
    registry_members = [orch_principal, spec_principal]
    target_policies = {
        "mcp_servers": {
            "corporate-email": [spec_principal],
            "income-verification": [spec_principal],
            "legacy-dms-mcp-dev": [spec_principal],
        },
        "agents": {
            "ai-coe-mortgage-specialist-agent-dev": [orch_principal],
        },
    }

    _, calls = _run_grant_script_with_mock_gcloud(
        tmp_path,
        iap_members=registry_members,
        target_policies=target_policies,
    )

    # 1 registry-root call + 3 mcp-server calls + 1 agent call = 5 calls
    assert len(calls) == 5

    # First call is the registry root (no --mcp-server or --agent flag), scoped to system endpoints
    root_call = calls[0]
    assert _is_root_call(root_call)
    _assert_conditional_root_policy(root_call["policy"], registry_members)

    # Map remaining calls by target flag; per-target policies stay unconditioned
    by_flag = {}
    for c in calls[1:]:
        target_flags = [a for a in c["args"] if a.startswith("--mcp-server=") or a.startswith("--agent=")]
        assert len(target_flags) == 1
        assert c["policy"] == {"bindings": [{"role": "roles/iap.egressor", "members": c["policy"]["bindings"][0]["members"]}]}
        by_flag[target_flags[0]] = c["policy"]["bindings"][0]["members"]

    assert by_flag["--mcp-server=corporate-email"] == [spec_principal]
    assert by_flag["--mcp-server=income-verification"] == [spec_principal]
    assert by_flag["--mcp-server=legacy-dms-mcp-dev"] == [spec_principal]
    assert by_flag["--agent=ai-coe-mortgage-specialist-agent-dev"] == [orch_principal]

    # Verify orchestrator is never granted egress to any MCP server
    for flag, members in by_flag.items():
        if flag.startswith("--mcp-server="):
            assert orch_principal not in members


def test_grant_iap_egress_script_layer4_bootstrap_without_target_policies(tmp_path: pathlib.Path):
    """When IAP_TARGET_POLICIES_JSON is omitted, grant_iap_egress.sh only sets the conditional registry root policy."""
    registry_members = [
        "principalSet://agents.global.org-123456.system.id.goog/attribute.platformContainer/aiplatform/projects/111"
    ]
    _, calls = _run_grant_script_with_mock_gcloud(
        tmp_path,
        iap_members=registry_members,
        target_policies=None,
    )
    assert len(calls) == 1
    assert _is_root_call(calls[0])
    _assert_conditional_root_policy(calls[0]["policy"], registry_members)


def test_grant_iap_egress_script_never_writes_unconditioned_root_binding(tmp_path: pathlib.Path):
    """No registry-root binding may reach a principalSet (or anyone) without the system-endpoints condition."""
    registry_members = [
        "principalSet://agents.global.org-123456.system.id.goog/attribute.platformContainer/aiplatform/projects/111",
        "principalSet://agents.global.org-123456.system.id.goog/attribute.container/projects/111",
        "serviceAccount:service-111@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
    ]
    spec_principal = (
        "principal://agents.global.org-123456.system.id.goog/"
        "resources/aiplatform/projects/222/locations/us-central1/reasoningEngines/spec-1"
    )
    _, calls = _run_grant_script_with_mock_gcloud(
        tmp_path,
        iap_members=registry_members,
        target_policies={"mcp_servers": {"legacy-dms": [spec_principal]}},
    )
    root_calls = [c for c in calls if _is_root_call(c)]
    assert len(root_calls) == 1
    for call in root_calls:
        assert call["policy"]["version"] == 3
        for binding in call["policy"]["bindings"]:
            assert binding.get("condition", {}).get("expression") == ROOT_CONDITION_EXPRESSION, binding
    # principalSets appear only in the conditional root binding, never in a per-target policy
    for call in calls:
        if not _is_root_call(call):
            for binding in call["policy"]["bindings"]:
                assert not any(m.startswith("principalSet://") for m in binding["members"])


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"GOVERNANCE_PROJECT_NUMBER": None}, "GOVERNANCE_PROJECT_NUMBER"),
        ({"GOVERNANCE_PROJECT_NUMBER": "esm-dev-governance"}, "numeric project number"),
        ({"REGION": 'us-central1") || true || ("'}, "REGION"),
    ],
    ids=["missing-number", "project-id-instead-of-number", "region-injection"],
)
def test_grant_iap_egress_script_rejects_bad_root_scope_inputs(tmp_path: pathlib.Path, override: dict, message: str):
    """Without a valid project number and region the script writes nothing (no unscoped root fallback)."""
    res, calls = _run_grant_script_with_mock_gcloud(
        tmp_path,
        iap_members=["principalSet://agents.global.org-123456.system.id.goog/attribute.container/projects/111"],
        extra_env=override,
        check=False,
    )
    assert res.returncode != 0
    assert message in res.stderr
    assert calls == []
