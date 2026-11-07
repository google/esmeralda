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

"""Offline Terraform unit tests for Layer 4 GEAP Semantic Governance (NLC) & UAP policies."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GOVERNANCE_MODULE = REPO_ROOT / "infrastructure/modules/4-governance"
CAPABILITIES_TF = GOVERNANCE_MODULE / "capabilities.tf"
VARIABLES_TF = GOVERNANCE_MODULE / "variables.tf"
SEMANTIC_GOVERNANCE_MODULE = GOVERNANCE_MODULE / "modules/7_semantic_governance"


def tf_binary() -> str:
    """Resolve Terraform or OpenTofu binary from TF_BIN, ~/.terraform/bin, or PATH."""
    if os.environ.get("TF_BIN"):
        found = shutil.which(os.environ["TF_BIN"])
        if found:
            return found
    user_tf = pathlib.Path.home() / ".terraform/bin/terraform"
    for candidate in (os.fspath(user_tf), "terraform", "tofu"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.exit("tests/governance requires `terraform` or `tofu` on PATH (or TF_BIN)", returncode=2)


def _squash(text: str) -> str:
    """Normalize whitespace across Terraform line-wrapped diagnostics."""
    return " ".join(text.split())


def _run_tf(workdir: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [tf_binary(), f"-chdir={workdir}", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "TF_IN_AUTOMATION": "1"},
    )


def _ensure_governance_providers() -> pathlib.Path:
    """Initialize `4-governance` once if needed and return its `.terraform/providers` directory."""
    providers_dir = GOVERNANCE_MODULE / ".terraform/providers"
    if not providers_dir.is_dir():
        init_res = _run_tf(GOVERNANCE_MODULE, "init", "-backend=false", "-input=false", "-no-color")
        assert init_res.returncode == 0, init_res.stdout + init_res.stderr
    return providers_dir


def evaluate_semantic_governance(
    workdir: pathlib.Path,
    tfvars: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate `7_semantic_governance` offline with the given variables and return outputs."""
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SEMANTIC_GOVERNANCE_MODULE, workdir, dirs_exist_ok=True)
    if tfvars is not None:
        (workdir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

    providers_dir = _ensure_governance_providers()
    init_res = _run_tf(
        workdir,
        "init",
        "-backend=false",
        "-input=false",
        "-no-color",
        f"-plugin-dir={providers_dir}",
    )
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    apply_res = _run_tf(workdir, "apply", "-auto-approve", "-input=false", "-no-color")
    if apply_res.returncode != 0:
        raise AssertionError(apply_res.stdout + "\n" + apply_res.stderr)

    out_res = _run_tf(workdir, "output", "-json")
    assert out_res.returncode == 0, out_res.stdout + out_res.stderr
    raw = json.loads(out_res.stdout)
    return {k: v["value"] for k, v in raw.items()}


def test_semantic_governance_submodule_validates(tmp_path: pathlib.Path):
    """The `7_semantic_governance` submodule initializes and passes `terraform validate` cleanly."""
    workdir = tmp_path / "validate-submodule"
    shutil.copytree(SEMANTIC_GOVERNANCE_MODULE, workdir, dirs_exist_ok=True)
    providers_dir = _ensure_governance_providers()

    init_res = _run_tf(
        workdir,
        "init",
        "-backend=false",
        "-input=false",
        "-no-color",
        f"-plugin-dir={providers_dir}",
    )
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    val_res = _run_tf(workdir, "validate", "-no-color")
    assert val_res.returncode == 0, val_res.stdout + val_res.stderr


def test_default_nlc_policies_cover_all_mcp_servers(tmp_path: pathlib.Path):
    """Default NLC policies govern `corporate-email`, `legacy-dms`, and `income-verification`."""
    outputs = evaluate_semantic_governance(tmp_path / "default-nlc")
    assert outputs["enabled"] is True
    assert outputs["policy_secret_id"] == ""

    policies = outputs["semantic_governance_policies"]
    assert set(policies.keys()) == {
        "corporate-email-nlc",
        "legacy-dms-nlc",
        "income-verification-nlc",
    }

    email_nlc = policies["corporate-email-nlc"]
    assert email_nlc["target_mcp_server"] == "corporate-email"
    assert email_nlc["tools"] == ["send_email", "read_email"]
    assert email_nlc["principal"] == "ai-coe-mortgage-specialist"
    assert email_nlc["enforcement_mode"] == "ENFORCE"
    assert any("@megabank.com" in c for c in email_nlc["natural_language_constraints"])
    assert any(
        "Social Security Numbers (SSNs)" in c and "XXX-XX-XXXX" in c
        for c in email_nlc["natural_language_constraints"]
    )

    dms_nlc = policies["legacy-dms-nlc"]
    assert dms_nlc["target_mcp_server"] == "legacy-dms"
    assert dms_nlc["tools"] == ["search_documents", "get_document"]
    assert dms_nlc["principal"] == "ai-coe-mortgage-specialist"
    assert any(
        "initiating user request" in c for c in dms_nlc["natural_language_constraints"]
    )
    assert any(
        "Deny bulk retrieval" in c for c in dms_nlc["natural_language_constraints"]
    )

    income_nlc = policies["income-verification-nlc"]
    assert income_nlc["target_mcp_server"] == "income-verification"
    assert income_nlc["tools"] == ["verify_applicant"]
    assert income_nlc["principal"] == "ai-coe-mortgage-specialist"
    assert any(
        "first_name and last_name" in c for c in income_nlc["natural_language_constraints"]
    )


def test_uap_bindings_enforce_least_privilege_separation(tmp_path: pathlib.Path):
    """UAP bindings isolate A2A orchestrator-to-specialist calls from specialist-to-MCP calls."""
    outputs = evaluate_semantic_governance(tmp_path / "uap-bindings")
    uaps = outputs["uap_bindings"]

    assert set(uaps.keys()) == {
        "a2a-ai-coe-mortgage-specialist",
        "mcp-corporate-email",
        "mcp-legacy-dms",
        "mcp-income-verification",
    }

    a2a_uap = uaps["a2a-ai-coe-mortgage-specialist"]
    assert a2a_uap["protocol"] == "a2a"
    assert a2a_uap["target_service"] == "ai-coe-mortgage-specialist"
    assert a2a_uap["allowed_principals"] == ["cx-mortgage-orchestrator"]
    assert a2a_uap["role"] == "roles/iap.egressor"

    for mcp_key, target_service, policy_ref in (
        ("mcp-corporate-email", "corporate-email", "corporate-email-nlc"),
        ("mcp-legacy-dms", "legacy-dms", "legacy-dms-nlc"),
        ("mcp-income-verification", "income-verification", "income-verification-nlc"),
    ):
        mcp_uap = uaps[mcp_key]
        assert mcp_uap["protocol"] == "mcp"
        assert mcp_uap["target_service"] == target_service
        assert mcp_uap["allowed_principals"] == ["ai-coe-mortgage-specialist"]
        assert "cx-mortgage-orchestrator" in mcp_uap["denied_principals"]
        assert mcp_uap["semantic_policy_refs"] == [policy_ref]


def test_policy_bundle_json_and_custom_policy_merge(tmp_path: pathlib.Path):
    """`policy_bundle_json` serializes the full bundle and merges custom NLC policies."""
    custom = {
        "credit-bureau-nlc": {
            "target_mcp_server": "credit-bureau",
            "tools": ["pull_soft_credit"],
            "principal": "ai-coe-mortgage-specialist",
            "enforcement_mode": "DRY_RUN",
            "natural_language_constraints": [
                "Allow pull_soft_credit only when explicit borrower consent is recorded in the session."
            ],
        }
    }
    outputs = evaluate_semantic_governance(
        tmp_path / "custom-merge",
        {"custom_policies": custom, "environment": "prd"},
    )
    bundle = json.loads(outputs["policy_bundle_json"])
    assert bundle["schema_version"] == "geap.semantic-governance/v1"
    assert bundle["environment"] == "prd"
    assert "credit-bureau-nlc" in bundle["semantic_governance_policies"]
    assert (
        bundle["semantic_governance_policies"]["credit-bureau-nlc"]["enforcement_mode"]
        == "DRY_RUN"
    )
    assert "corporate-email-nlc" in bundle["semantic_governance_policies"]


def test_wildcard_principal_rejected_by_precondition(tmp_path: pathlib.Path):
    """Wildcard principals (`*` or `/*`) in NLC policies are rejected by the module precondition."""
    bad_custom = {
        "wildcard-nlc": {
            "target_mcp_server": "corporate-email",
            "tools": ["send_email"],
            "principal": "principalSet://iam.googleapis.com/organizations/123/*",
            "natural_language_constraints": ["Allow send_email to internal domains."],
        }
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_semantic_governance(tmp_path / "wildcard-principal", {"custom_policies": bad_custom})

    assert "wildcard principals are forbidden" in _squash(str(exc_info.value))


def test_invalid_enforcement_mode_rejected(tmp_path: pathlib.Path):
    """Only `ENFORCE` and `DRY_RUN` enforcement modes are accepted."""
    with pytest.raises(AssertionError) as exc_info:
        evaluate_semantic_governance(
            tmp_path / "bad-mode",
            {"enforcement_mode": "PERMISSIVE"},
        )

    assert 'enforcement_mode must be either "ENFORCE" or "DRY_RUN"' in _squash(
        str(exc_info.value)
    )


def test_capabilities_integration_and_manifest_output(tmp_path: pathlib.Path):
    """`capabilities.tf` wires `module.semantic_governance` and exposes `semantic_governance_manifest`."""
    workdir = tmp_path / "capabilities-integration"
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CAPABILITIES_TF, workdir / "capabilities.tf")
    shutil.copy2(VARIABLES_TF, workdir / "variables.tf")
    shutil.copytree(
        SEMANTIC_GOVERNANCE_MODULE,
        workdir / "modules/7_semantic_governance",
        dirs_exist_ok=True,
    )
    # Include only the semantic_governance_manifest output from outputs.tf
    (workdir / "manifest_output.tf").write_text(
        'output "semantic_governance_manifest" {\n'
        "  value = module.semantic_governance.manifest\n"
        "}\n"
    )
    (workdir / "terraform.tfvars.json").write_text(
        json.dumps(
            {
                "governance_project_id": "esm-dev-governance-mock",
                "internal_root_ca_pem": "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----",
            },
            indent=2,
        )
    )

    providers_dir = _ensure_governance_providers()
    init_res = _run_tf(
        workdir,
        "init",
        "-backend=false",
        "-input=false",
        "-no-color",
        f"-plugin-dir={providers_dir}",
    )
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    apply_res = _run_tf(workdir, "apply", "-auto-approve", "-input=false", "-no-color")
    assert apply_res.returncode == 0, apply_res.stdout + apply_res.stderr

    out_res = _run_tf(workdir, "output", "-json")
    assert out_res.returncode == 0, out_res.stdout + out_res.stderr
    outputs = {k: v["value"] for k, v in json.loads(out_res.stdout).items()}

    routing = outputs["effective_capability_routing"]
    assert routing["content_safety"]["semantic_governance_enabled"] is True
    assert routing["egress_allowlist"]["uap_enforcement_enabled"] is True

    manifest = outputs["semantic_governance_manifest"]
    assert manifest["enabled"] is True
    assert manifest["schema_version"] == "geap.semantic-governance/v1"
    assert set(manifest["semantic_governance_policies"].keys()) == {
        "corporate-email-nlc",
        "legacy-dms-nlc",
        "income-verification-nlc",
    }
