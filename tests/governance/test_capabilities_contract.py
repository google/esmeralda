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

"""Offline Terraform validation tests for Layer 4 capability-based governance."""

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

EXPECTED_DEFAULT_CAPABILITIES = {
    "egress_allowlist": "agent_gateway",
    "content_safety": "agent_gateway",
    "partner_access": "none",
    "token_quota": "none",
    "usage_metering": "agent_gateway",
    "cross_cloud": "agent_gateway",
}


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


def evaluate_capabilities(workdir: pathlib.Path, governance_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate `capabilities.tf` offline with the given `governance_config` and return outputs."""
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CAPABILITIES_TF, workdir / "capabilities.tf")
    shutil.copy2(VARIABLES_TF, workdir / "variables.tf")
    shutil.copytree(
        SEMANTIC_GOVERNANCE_MODULE,
        workdir / "modules/7_semantic_governance",
        dirs_exist_ok=True,
    )
    tfvars: dict[str, Any] = {
        "governance_project_id": "esm-dev-governance-mock",
        "internal_root_ca_pem": "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----",
    }
    if governance_config is not None:
        tfvars["governance_config"] = governance_config
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


def test_full_governance_module_validates():
    """The complete `infrastructure/modules/4-governance` module initializes and validates cleanly."""
    init_res = _run_tf(GOVERNANCE_MODULE, "init", "-backend=false", "-input=false", "-no-color")
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    val_res = _run_tf(GOVERNANCE_MODULE, "validate", "-no-color")
    assert val_res.returncode == 0, val_res.stdout + val_res.stderr


def test_default_agw_only_configuration_passes(tmp_path: pathlib.Path):
    """Omitting `governance_config` defaults to Agent Gateway governance with Apigee disabled."""
    outputs = evaluate_capabilities(tmp_path / "defaults")
    assert outputs["apigee_enabled"] is False
    assert outputs["capabilities"] == EXPECTED_DEFAULT_CAPABILITIES


def test_explicit_env_yaml_agw_configuration_passes(tmp_path: pathlib.Path):
    """The default `governance` block in `env.yaml` (Agent Gateway providers, Apigee off) passes."""
    config = {
        "apigee": {
            "enabled": False,
            "byo": False,
            "environment_type": "BASE",
        },
        "capabilities": dict(EXPECTED_DEFAULT_CAPABILITIES),
    }
    outputs = evaluate_capabilities(tmp_path / "explicit", config)
    assert outputs["apigee_enabled"] is False
    assert outputs["capabilities"] == EXPECTED_DEFAULT_CAPABILITIES


@pytest.mark.parametrize(
    "capability",
    [
        "partner_access",
        "token_quota",
        "usage_metering",
        "cross_cloud",
        "content_safety",
        "egress_allowlist",
    ],
)
def test_apigee_capability_with_apigee_disabled_fails(tmp_path: pathlib.Path, capability: str):
    """Any capability set to `apigee` while `apigee.enabled = false` is rejected."""
    config = {
        "apigee": {"enabled": False, "byo": False, "environment_type": "INTERMEDIATE"},
        "capabilities": {capability: "apigee"},
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_capabilities(tmp_path / f"disabled-{capability}", config)

    assert 'Capabilities cannot use provider "apigee" when governance_config.apigee.enabled is false' in _squash(
        str(exc_info.value)
    )


def test_token_quota_apigee_with_base_environment_fails(tmp_path: pathlib.Path):
    """`token_quota = "apigee"` fails when `environment_type = "BASE"` because LLMTokenQuota requires INTERMEDIATE+."""
    config = {
        "apigee": {"enabled": True, "byo": False, "environment_type": "BASE"},
        "capabilities": {
            "partner_access": "apigee",
            "token_quota": "apigee",
        },
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_capabilities(tmp_path / "base-quota", config)

    assert 'requires governance_config.apigee.environment_type to be "INTERMEDIATE" or "COMPREHENSIVE"' in _squash(
        str(exc_info.value)
    )


@pytest.mark.parametrize("env_type", ["INTERMEDIATE", "COMPREHENSIVE"])
def test_token_quota_apigee_with_supported_environment_passes(tmp_path: pathlib.Path, env_type: str):
    """`token_quota = "apigee"` succeeds when `apigee.enabled = true` and `environment_type` is INTERMEDIATE+."""
    config = {
        "apigee": {"enabled": True, "byo": False, "environment_type": env_type},
        "capabilities": {
            "partner_access": "apigee",
            "token_quota": "apigee",
            "usage_metering": "apigee",
            "cross_cloud": "apigee",
        },
    }
    outputs = evaluate_capabilities(tmp_path / f"quota-{env_type.lower()}", config)
    assert outputs["apigee_enabled"] is True
    assert outputs["capabilities"]["partner_access"] == "apigee"
    assert outputs["capabilities"]["token_quota"] == "apigee"
    assert outputs["capabilities"]["usage_metering"] == "apigee"
    assert outputs["capabilities"]["egress_allowlist"] == "agent_gateway"


def test_partner_access_apigee_on_base_without_token_quota_passes(tmp_path: pathlib.Path):
    """`partner_access = "apigee"` is allowed on a `BASE` environment as long as `token_quota` is not `apigee`."""
    config = {
        "apigee": {"enabled": True, "byo": False, "environment_type": "BASE"},
        "capabilities": {
            "partner_access": "apigee",
            "token_quota": "none",
            "cross_cloud": "apigee",
        },
    }
    outputs = evaluate_capabilities(tmp_path / "partner-base", config)
    assert outputs["apigee_enabled"] is True
    assert outputs["capabilities"]["partner_access"] == "apigee"
    assert outputs["capabilities"]["token_quota"] == "none"


def test_partner_access_apigee_requires_the_cross_cloud_shared_flow(tmp_path: pathlib.Path):
    """The partner proxies call the cross-cloud shared flow: partner_access = "apigee" needs cross_cloud = "apigee"."""
    config = {
        "apigee": {"enabled": True, "byo": False, "environment_type": "INTERMEDIATE"},
        "capabilities": {"partner_access": "apigee", "cross_cloud": "agent_gateway"},
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_capabilities(tmp_path / "partner-no-cross-cloud", config)

    assert 'partner_access = "apigee" requires cross_cloud = "apigee"' in _squash(str(exc_info.value))


def test_invalid_environment_type_fails(tmp_path: pathlib.Path):
    """Unknown Apigee environment types are rejected."""
    config = {
        "apigee": {"enabled": False, "byo": False, "environment_type": "EVALUATION"},
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_capabilities(tmp_path / "bad-env-type", config)

    assert "governance_config.apigee.environment_type must be one of: BASE, INTERMEDIATE, COMPREHENSIVE" in _squash(
        str(exc_info.value)
    )


def test_invalid_capability_provider_fails(tmp_path: pathlib.Path):
    """Unknown capability provider names are rejected."""
    config = {
        "capabilities": {"partner_access": "kong"},
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_capabilities(tmp_path / "bad-provider", config)

    expected = (
        'Every capability in governance_config.capabilities must use an allowed provider: '
        '"none", "agent_gateway", or "apigee"'
    )
    assert expected in _squash(str(exc_info.value))


def test_egress_allowlist_cannot_be_none(tmp_path: pathlib.Path):
    """`egress_allowlist = "none"` is rejected because agent egress must always be governed."""
    config = {
        "capabilities": {"egress_allowlist": "none"},
    }
    with pytest.raises(AssertionError) as exc_info:
        evaluate_capabilities(tmp_path / "ungoverned-egress", config)

    assert 'governance_config.capabilities.egress_allowlist cannot be "none"' in _squash(str(exc_info.value))
