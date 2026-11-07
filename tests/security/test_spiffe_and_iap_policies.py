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
IAP_EGRESS_MOD_DIR = REPO_ROOT / "infrastructure/modules/5-workloads/services/iap-egress"
GRANT_IAP_SCRIPT = REPO_ROOT / "infrastructure/modules/_shared/scripts/grant_iap_egress.sh"
DEV_IAP_TG = REPO_ROOT / "infrastructure/live/dev/layer-5-workloads/services/iap-egress/terragrunt.hcl"
PRD_IAP_TG = REPO_ROOT / "infrastructure/live/prd/layer-5-workloads/services/iap-egress/terragrunt.hcl"


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
    assert 'resource "google_bigquery_dataset_iam_member" "agent_identity_bq_writer"' in main_tf
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


@pytest.mark.parametrize("tg_path", [DEV_IAP_TG, PRD_IAP_TG], ids=["dev", "prd"])
def test_iap_egress_terragrunt_enforces_least_privilege_per_target_callers(tg_path: pathlib.Path):
    """iap-egress terragrunt units must separate specialist callers (orchestrator) from MCP callers (specialist)."""
    content = tg_path.read_text()

    assert "target_iap_members" in content
    assert '"ai-coe-mortgage-specialist" = local.specialist_agent_callers' in content
    for mcp_id in ("corporate-email", "income-verification", "legacy-dms"):
        assert f'"{mcp_id}"' in content
        assert "local.mcp_server_callers" in content

    # Verify specialist_agent_callers only includes orchestrator + test_vm, never specialist
    spec_block = content.split("specialist_agent_callers = [", 1)[1].split("]", 1)[0]
    assert "dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal" in spec_block
    assert "cx_mortgage_orchestrator_sa_email" in spec_block
    assert "ai_coe_mortgage_specialist" not in spec_block

    # Verify mcp_server_callers only includes specialist + mcp_invoker + test_vm, never orchestrator
    mcp_block = content.split("mcp_server_callers = [", 1)[1].split("]", 1)[0]
    assert "dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal" in mcp_block
    assert "ai_coe_mortgage_specialist_sa_email" in mcp_block
    assert "cx_mortgage_orchestrator" not in mcp_block


def _run_grant_script_with_mock_gcloud(
    tmp_path: pathlib.Path,
    *,
    iap_members: list[str],
    target_policies: dict | None = None,
) -> list[dict]:
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
        "REGION": "us-central1",
        "IAP_MEMBERS_JSON": json.dumps(iap_members),
    }
    if target_policies is not None:
        env["IAP_TARGET_POLICIES_JSON"] = json.dumps(target_policies)
    else:
        env.pop("IAP_TARGET_POLICIES_JSON", None)

    res = subprocess.run(
        ["bash", str(GRANT_IAP_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    return [json.loads(line) for line in calls_log.read_text().splitlines() if line.strip()]


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

    calls = _run_grant_script_with_mock_gcloud(
        tmp_path,
        iap_members=registry_members,
        target_policies=target_policies,
    )

    # 1 registry-wide call + 3 mcp-server calls + 1 agent call = 5 calls
    assert len(calls) == 5

    # First call is registry-wide (no --mcp-server or --agent flag)
    root_call = calls[0]
    assert not any(a.startswith("--mcp-server=") or a.startswith("--agent=") for a in root_call["args"])
    assert root_call["policy"]["bindings"] == [
        {"role": "roles/iap.egressor", "members": registry_members}
    ]

    # Map remaining calls by target flag
    by_flag = {}
    for c in calls[1:]:
        target_flags = [a for a in c["args"] if a.startswith("--mcp-server=") or a.startswith("--agent=")]
        assert len(target_flags) == 1
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
    """When IAP_TARGET_POLICIES_JSON is omitted, grant_iap_egress.sh only sets the registry root policy."""
    registry_members = [
        "principalSet://agents.global.org-123456.system.id.goog/attribute.platformContainer/aiplatform/projects/111"
    ]
    calls = _run_grant_script_with_mock_gcloud(
        tmp_path,
        iap_members=registry_members,
        target_policies=None,
    )
    assert len(calls) == 1
    assert not any(a.startswith("--mcp-server=") or a.startswith("--agent=") for a in calls[0]["args"])
    assert calls[0]["policy"]["bindings"] == [
        {"role": "roles/iap.egressor", "members": registry_members}
    ]
