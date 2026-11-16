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

"""Offline conformance tests for the esmeralda-ui Cloud Run module, CLI subcommand, and TypeScript suite."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "packages" / "esmeralda" / "src"))

from esmeralda import cli  # noqa: E402

UI_MODULE_DIR = REPO_ROOT / "infrastructure" / "modules" / "5-workloads" / "services" / "esmeralda-ui"
UI_APP_DIR = REPO_ROOT / "apps" / "services" / "esmeralda-ui"


def test_esmeralda_ui_terraform_module_supports_direct_iap_and_gcip() -> None:
    main_tf = (UI_MODULE_DIR / "main.tf").read_text(encoding="utf-8")
    variables_tf = (UI_MODULE_DIR / "variables.tf").read_text(encoding="utf-8")

    # 1. Digest-pinned image deployment
    assert "image = local.image_by_digest" in main_tf
    assert 'data "google_artifact_registry_docker_image" "image"' in main_tf

    # 2. Direct Cloud Run IAP + IAP Service Agent invoker + iap_accessors
    assert "iap_enabled" in main_tf
    assert "gcp-sa-iap.iam.gserviceaccount.com" in main_tf
    assert 'resource "google_iap_web_cloud_run_service_iam_member" "iap_accessors"' in main_tf

    # 3. Identity Platform (GCIP) integration on Cloud Run IAP
    assert 'variable "identity_platform"' in variables_tf
    assert 'resource "google_iap_settings" "gcip_settings"' in main_tf
    assert "gcip_settings" in main_tf

    # 4. Internal Private CA trust for Node.js
    assert 'name  = "NODE_EXTRA_CA_CERTS"' in main_tf

    # 5. Governance runtime catalog secret accessor
    assert 'resource "google_secret_manager_secret_iam_member" "runtime_catalog_accessor"' in main_tf


def test_esmeralda_ui_terraform_validate(tmp_path: Path) -> None:
    tf_bin = shutil.which("terraform") or shutil.which("tofu")
    if not tf_bin:
        pytest.skip("terraform/tofu binary not found")

    work_dir = tmp_path / "esmeralda-ui-tf"
    shutil.copytree(UI_MODULE_DIR, work_dir)
    init_res = subprocess.run(
        [tf_bin, "init", "-backend=false", "-input=false"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if init_res.returncode != 0:
        pytest.skip(f"Offline terraform init skipped (no cached provider): {init_res.stderr}")

    val_res = subprocess.run(
        [tf_bin, "validate", "-no-color"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Success!" in val_res.stdout


def test_esmeralda_cli_ui_subcommand_parser() -> None:
    parser = cli._parser()
    args = parser.parse_args(["ui", "--env", "dev", "--port", "8090", "--url"])
    assert args.cmd == "ui"
    assert args.env == "dev"
    assert args.port == 8090
    assert args.url is True
    assert args.local is False


def test_esmeralda_ui_typescript_suite() -> None:
    node_bin = shutil.which("node")
    if not node_bin:
        nvm_nodes = sorted((Path.home() / ".nvm" / "versions" / "node").glob("*/bin/node"))
        if nvm_nodes:
            node_bin = str(nvm_nodes[-1])
    if not node_bin or not Path(node_bin).exists():
        pytest.skip("Node.js >= 22.6 not available")

    res = subprocess.run(
        [
            node_bin,
            "--experimental-strip-types",
            "--test",
            str(UI_APP_DIR / "tests" / "esmeralda_ui.test.ts"),
        ],
        cwd=UI_APP_DIR,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"TypeScript tests failed:\n{res.stdout}\n{res.stderr}"
