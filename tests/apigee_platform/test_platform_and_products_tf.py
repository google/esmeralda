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

"""Offline contract and validation tests for the Apigee platform and products Terraform modules."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_MODULE_DIR = (
    REPO_ROOT / "infrastructure" / "modules" / "5-workloads" / "apigee" / "platform"
)
PRODUCTS_MODULE_DIR = (
    REPO_ROOT / "infrastructure" / "modules" / "5-workloads" / "apigee" / "products"
)
GATEWAY_APIGEE_DIR = (
    REPO_ROOT / "infrastructure" / "modules" / "5-workloads" / "gateways" / "apigee"
)
LIVE_ENVS = ("dev", "prd")


def _find_terraform_bin() -> str:
    env_bin = os.environ.get("TF_BIN")
    if env_bin and shutil.which(env_bin):
        return env_bin
    for candidate in (
        shutil.which("terraform"),
        str(Path.home() / ".terraform" / "bin" / "terraform"),
        shutil.which("tofu"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("Neither terraform nor tofu binary is available on this machine.")


def _run_tf_validate(module_dir: Path) -> None:
    tf_bin = _find_terraform_bin()
    init_res = subprocess.run(
        [tf_bin, f"-chdir={module_dir}", "init", "-backend=false", "-input=false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert init_res.returncode == 0, (
        f"terraform init failed in {module_dir}:\n"
        f"STDOUT:\n{init_res.stdout}\nSTDERR:\n{init_res.stderr}"
    )

    val_res = subprocess.run(
        [tf_bin, f"-chdir={module_dir}", "validate", "-no-color"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert val_res.returncode == 0, (
        f"terraform validate failed in {module_dir}:\n"
        f"STDOUT:\n{val_res.stdout}\nSTDERR:\n{val_res.stderr}"
    )


def test_terraform_validate_apigee_platform() -> None:
    """Verify `terraform init -backend=false && terraform validate` passes on apigee/platform."""
    _run_tf_validate(PLATFORM_MODULE_DIR)


def test_terraform_validate_apigee_products() -> None:
    """Verify `terraform init -backend=false && terraform validate` passes on apigee/products."""
    _run_tf_validate(PRODUCTS_MODULE_DIR)


def test_terraform_validate_gateway_apigee_wrapper() -> None:
    """Verify `terraform init -backend=false && terraform validate` passes on gateways/apigee."""
    _run_tf_validate(GATEWAY_APIGEE_DIR)


def test_platform_uses_non_peering_psc_architecture() -> None:
    """Verify apigee/platform sets disable_vpc_peering = true and avoids legacy VPC peering."""
    main_tf = (PLATFORM_MODULE_DIR / "main.tf").read_text(encoding="utf-8")
    variables_tf = (PLATFORM_MODULE_DIR / "variables.tf").read_text(encoding="utf-8")

    assert re.search(r"disable_vpc_peering\s*=\s*true", main_tf), (
        "google_apigee_organization must set disable_vpc_peering = true"
    )
    assert "authorized_network" not in main_tf, (
        "google_apigee_organization must not set authorized_network in PSC non-peering mode"
    )
    assert "peering_cidr_range" not in main_tf, (
        "google_apigee_instance must not set peering_cidr_range in PSC non-peering mode"
    )
    assert re.search(r'retention\s*=\s*var\.billing_type\s*==\s*"EVALUATION"\s*\?\s*null\s*:\s*"MINIMUM"', main_tf), (
        "Paid Apigee organizations must set retention = MINIMUM for clean teardown"
    )
    assert 'variable "byo_apigee_org"' in variables_tf
    assert 'variable "existing_org_id"' in variables_tf
    assert 'variable "existing_instance_service_attachment"' in variables_tf


def test_platform_southbound_target_server_enforces_tls_verification() -> None:
    """Verify southbound target server ts-esmeralda-entrypoint enforces TLS verification."""
    main_tf = (PLATFORM_MODULE_DIR / "main.tf").read_text(encoding="utf-8")

    assert 'resource "google_apigee_endpoint_attachment" "entrypoint"' in main_tf
    assert 'resource "google_apigee_target_server" "entrypoint"' in main_tf
    assert '"ts-esmeralda-entrypoint"' in main_tf
    assert re.search(r"port\s*=\s*443", main_tf)

    ssl_block_match = re.search(r"s_sl_info\s*\{([^}]+)\}", main_tf, re.DOTALL)
    assert ssl_block_match is not None, "google_apigee_target_server must configure s_sl_info"
    ssl_block = ssl_block_match.group(1)
    assert re.search(r"enabled\s*=\s*true", ssl_block)
    assert re.search(r"enforce\s*=\s*true", ssl_block)
    assert re.search(r"ignore_validation_errors\s*=\s*false", ssl_block)


def test_platform_grants_token_creator_to_apigee_service_agent() -> None:
    """Verify Apigee service agent receives roles/iam.serviceAccountTokenCreator on proxy runtime SA."""
    main_tf = (PLATFORM_MODULE_DIR / "main.tf").read_text(encoding="utf-8")

    assert 'resource "google_service_account" "apigee_proxy_runtime"' in main_tf
    assert 'account_id   = "sa-esmeralda-apigee-proxy-${var.environment}"' in main_tf
    assert 'resource "google_service_account_iam_member" "apigee_service_agent_token_creator"' in main_tf
    assert '"roles/iam.serviceAccountTokenCreator"' in main_tf
    assert "gcp-sa-apigee.iam.gserviceaccount.com" in main_tf


def test_platform_implements_entrypoint_gateway_contract() -> None:
    """Verify apigee/platform provisions PSC NEG + INTERNAL_MANAGED backend service and outputs contract."""
    main_tf = (PLATFORM_MODULE_DIR / "main.tf").read_text(encoding="utf-8")
    outputs_tf = (PLATFORM_MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")

    assert 'resource "google_compute_region_network_endpoint_group" "apigee_psc"' in main_tf
    assert 'network_endpoint_type = "PRIVATE_SERVICE_CONNECT"' in main_tf
    assert 'resource "google_compute_region_backend_service" "apigee"' in main_tf
    assert 'load_balancing_scheme = "INTERNAL_MANAGED"' in main_tf
    assert 'protocol              = "HTTPS"' in main_tf
    assert re.search(r"timeout_sec\s*=\s*300", main_tf)
    assert 'balancing_mode = "UTILIZATION"' in main_tf

    assert 'output "backend_service_id"' in outputs_tf
    assert 'output "routes"' in outputs_tf
    assert 'output "org_id"' in outputs_tf
    assert 'output "proxy_runtime_sa_email"' in outputs_tf


def test_products_creates_mcp_tool_scoping_quota_attributes_and_secrets() -> None:
    """Verify apigee/products creates API products with allowed_mcp_tools, quota attributes, and Secret Manager keys."""
    main_tf = (PRODUCTS_MODULE_DIR / "main.tf").read_text(encoding="utf-8")
    outputs_tf = (PRODUCTS_MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")

    assert 'resource "google_apigee_api_product" "product"' in main_tf
    for attr_name in (
        "access",
        "allowed_mcp_tools",
        "llm_quota_limit",
        "llm_quota_interval",
        "llm_quota_time_unit",
    ):
        assert f'name  = "{attr_name}"' in main_tf, (
            f"google_apigee_api_product must set custom attribute '{attr_name}'"
        )

    assert 'resource "google_apigee_developer" "developer"' in main_tf
    assert 'resource "google_apigee_developer_app" "consumer"' in main_tf
    assert 'name  = "owner_identity"' in main_tf

    assert 'resource "google_secret_manager_secret" "consumer_key"' in main_tf
    assert 'secret_id = "apigee-consumer-key-${each.key}-${var.environment}"' in main_tf
    assert 'resource "google_secret_manager_secret_version" "consumer_key"' in main_tf
    assert "sensitive(" in main_tf
    assert 'resource "google_secret_manager_secret_iam_member" "consumer_key_accessor"' in main_tf
    assert '"roles/secretmanager.secretAccessor"' in main_tf

    assert 'output "product_names"' in outputs_tf
    assert 'output "consumer_key_secret_ids"' in outputs_tf


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_live_terragrunt_units_excluded_by_default_and_define_partner_tiers(env: str) -> None:
    """Verify live dev/prd Terragrunt units exist, exclude when Apigee is disabled, and define partner tiers."""
    base_dir = REPO_ROOT / "infrastructure" / "live" / env / "layer-5-workloads" / "apigee"
    platform_hcl = (base_dir / "platform" / "terragrunt.hcl").read_text(encoding="utf-8")
    products_hcl = (base_dir / "products" / "terragrunt.hcl").read_text(encoding="utf-8")

    expected_exclude = (
        '!try(local.env.governance.apigee.enabled, false) && '
        '!contains(try(local.env.gateways.enabled, []), "apigee")'
    )
    assert expected_exclude in platform_hcl
    assert expected_exclude in products_hcl

    assert "esmeralda-partner-standard" in products_hcl
    assert "esmeralda-partner-premium" in products_hcl
    assert "partner-smoke-test" in products_hcl
