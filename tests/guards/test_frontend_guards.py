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

"""Human-facing frontends (``modules/5-workloads/frontends/*``) stay private, behind IAP, least privilege.

* No Cloud Run service in the repository takes public ingress (``INGRESS_TRAFFIC_ALL``); frontends use
  internal-and-load-balancer ingress behind the private entry point.
* IAP access goes to existing groups only: the ``iap_accessors`` validation rejects ``allUsers``,
  ``allAuthenticatedUsers``, ``user:`` and service accounts (``domain:`` only behind an explicit flag),
  evaluated offline with Terraform.
* Esmeralda never manages Cloud Identity groups or enables the Cloud Identity API.
* The console's service account holds only the roles listed here (dataset-scoped BigQuery read, job
  user, and the IAP / invoker bindings on its own resources): no project-wide viewer, no secrets.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infrastructure"
FRONTENDS = INFRA / "modules/5-workloads/frontends"
CONSOLE = FRONTENDS / "esmeralda-ui"
SKIP_DIRS = {".terraform", ".terragrunt-cache", ".local_states"}

# Every role the console module may grant, and on what.
CONSOLE_ROLES = {
    "roles/bigquery.dataViewer": "google_bigquery_dataset_iam_member",
    "roles/bigquery.jobUser": "google_project_iam_member",
    "roles/iap.httpsResourceAccessor": "google_iap_web_region_backend_service_iam_binding",
    "roles/run.invoker": "google_cloud_run_v2_service_iam_binding",
}


def _tf_files(base: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in base.rglob("*.tf") if not SKIP_DIRS.intersection(p.relative_to(base).parts))


def _hcl_files(base: pathlib.Path) -> list[pathlib.Path]:
    files = [*base.rglob("*.tf"), *base.rglob("*.hcl"), *base.rglob("*.yaml")]
    return sorted(p for p in files if not SKIP_DIRS.intersection(p.relative_to(base).parts))


def _resources(text: str) -> list[tuple[str, str, str]]:
    """``(type, name, body)`` of each top-level ``resource`` block (bodies end at a closing brace in column 0)."""
    return [(m.group(1), m.group(2), m.group(3)) for m in re.finditer(
        r'^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{(.*?)^\}', text, re.MULTILINE | re.DOTALL)]


# ── static guards ─────────────────────────────────────────────────────────────


def test_frontend_modules_exist():
    assert (CONSOLE / "main.tf").is_file()
    for env in ("dev", "prd"):
        assert (INFRA / f"live/{env}/layer-5-workloads/frontends/esmeralda-ui/terragrunt.hcl").is_file(), env


def test_no_public_cloud_run_ingress():
    offenders = [str(p.relative_to(REPO_ROOT)) for p in _tf_files(INFRA) if "INGRESS_TRAFFIC_ALL" in p.read_text()]
    assert not offenders, f"Cloud Run with public ingress (INGRESS_TRAFFIC_ALL): {offenders}"


def test_frontends_use_internal_load_balancer_ingress():
    services = [
        (path, body)
        for path in _tf_files(FRONTENDS)
        for rtype, _, body in _resources(path.read_text())
        if rtype == "google_cloud_run_v2_service"
    ]
    assert services, "no Cloud Run service under modules/5-workloads/frontends"
    for path, body in services:
        assert re.search(r'^\s*ingress\s*=\s*"INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"', body, re.MULTILINE), path


def test_frontends_have_iap_on_their_backend_service():
    for module in sorted(p for p in FRONTENDS.iterdir() if (p / "main.tf").is_file()):
        backends = [
            body for path in _tf_files(module) for rtype, _, body in _resources(path.read_text())
            if rtype == "google_compute_region_backend_service"
        ]
        assert backends, module
        for body in backends:
            assert re.search(r"iap\s*\{\s*enabled\s*=\s*true", body), f"{module}: backend service without IAP"


def test_no_cloud_identity_groups():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _hcl_files(INFRA)
        if re.search(r"google_cloud_identity_group|cloudidentity\.googleapis\.com", p.read_text())
    ]
    assert not offenders, f"Cloud Identity groups / API are managed outside Esmeralda: {offenders}"


def test_console_service_account_least_privilege():
    text = "\n".join(p.read_text() for p in _tf_files(CONSOLE))
    granted: dict[str, set[str]] = {}
    for rtype, name, body in _resources(text):
        if "_iam_" not in rtype:
            continue
        role = re.search(r'^\s*role\s*=\s*"([^"]+)"', body, re.MULTILINE)
        assert role, f"{rtype}.{name}: role must be a literal (no for_each over a role list)"
        granted.setdefault(role.group(1), set()).add(rtype)
    assert set(granted) <= set(CONSOLE_ROLES), f"unexpected roles: {sorted(set(granted) - set(CONSOLE_ROLES))}"
    for role, rtypes in granted.items():
        assert rtypes == {CONSOLE_ROLES[role]}, f"{role} granted through {sorted(rtypes)}"
    # Dataset-scoped read only, and nothing about logs or secrets.
    assert not re.search(r"secretmanager|logging\.viewer|logging\.privateLogViewer|roles/viewer|roles/editor", text)
    assert "google_secret_manager" not in text


def test_console_invoker_is_iap_only():
    text = (CONSOLE / "main.tf").read_text()
    invoker = next(body for rtype, _, body in _resources(text) if rtype == "google_cloud_run_v2_service_iam_binding")
    members = re.search(r"members\s*=\s*\[(.*?)\]", invoker, re.DOTALL)
    assert members and members.group(1).strip() == '"serviceAccount:${google_project_service_identity.iap.email}"'
    assert "allUsers" not in text and "allAuthenticatedUsers" not in text


def test_console_accessors_fail_closed_when_empty():
    text = (CONSOLE / "main.tf").read_text()
    assert "length(var.iap_accessors) > 0" in text
    assert "var.allow_domain_accessors || length(local.domain_accessors) == 0" in text


def test_frontend_units_off_unless_enabled():
    for env in ("dev", "prd"):
        unit = (INFRA / f"live/{env}/layer-5-workloads/frontends/esmeralda-ui/terragrunt.hcl").read_text()
        assert "!try(local.env_vars.locals.frontends.esmeralda_ui.enabled, false)" in unit, env
        env_yaml = (INFRA / f"live/{env}/env.yaml").read_text()
        block = re.search(r"esmeralda_ui\s*=\s*\{(.*?)\}", env_yaml, re.DOTALL)
        assert block, env
        if env == "prd":
            assert re.search(r"enabled\s*=\s*false", block.group(1)), "prd: the console is off by default"
        # Accessor groups are environment-specific: never committed to the public env files.
        assert re.search(r"iap_accessors\s*=\s*\[\s*\]", block.group(1)), env


def test_frontend_dev_prd_units_match():
    dev = (INFRA / "live/dev/layer-5-workloads/frontends/esmeralda-ui/terragrunt.hcl").read_text()
    prd = (INFRA / "live/prd/layer-5-workloads/frontends/esmeralda-ui/terragrunt.hcl").read_text()
    assert dev.replace("live/dev/", "live/prd/") == prd


# ── iap_accessors validation, evaluated offline ───────────────────────────────

REQUIRED = {
    "project_id": "esm-dev-gateway-mock",
    "region": "us-central1",
    "environment": "dev",
    "container_image": "example.invalid/esmeralda-ui@sha256:" + "0" * 64,
    "domain": "esmeralda.internal",
    "bq_project": "esm-dev-governance-mock",
    "telemetry_dataset_id": "esmeralda_telemetry_logs_dev",
}


def _tf_binary() -> str:
    if os.environ.get("TF_BIN") and shutil.which(os.environ["TF_BIN"]):
        return shutil.which(os.environ["TF_BIN"])  # type: ignore[return-value]
    for candidate in (os.fspath(pathlib.Path.home() / ".terraform/bin/terraform"), "terraform", "tofu"):
        if found := shutil.which(candidate):
            return found
    pytest.exit("tests/guards/test_frontend_guards.py requires `terraform` or `tofu` on PATH (or TF_BIN)", returncode=2)


def _validate_vars(workdir: pathlib.Path, tfvars: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """Only variables.tf (no providers, no resources): plan evaluates the variable validations."""
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CONSOLE / "variables.tf", workdir / "variables.tf")
    (workdir / "terraform.tfvars.json").write_text(json.dumps({**REQUIRED, **tfvars}))
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    tf = _tf_binary()
    init = subprocess.run([tf, f"-chdir={workdir}", "init", "-backend=false", "-input=false", "-no-color"],
                          capture_output=True, text=True, env=env)
    assert init.returncode == 0, init.stdout + init.stderr
    return subprocess.run([tf, f"-chdir={workdir}", "plan", "-input=false", "-no-color"],
                          capture_output=True, text=True, env=env)


@pytest.mark.parametrize("accessors", [
    [],
    ["group:console-viewers@example.com"],
    ["group:console-viewers@example.com", "group:auditors@example.com"],
    ["domain:example.com"],  # accepted here; the domain flag is enforced by a precondition in main.tf
])
def test_iap_accessors_accepted(tmp_path: pathlib.Path, accessors: list[str]):
    res = _validate_vars(tmp_path, {"iap_accessors": accessors})
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("accessors", [
    ["allUsers"],
    ["allAuthenticatedUsers"],
    ["user:someone@example.com"],
    ["serviceAccount:sa@example.iam.gserviceaccount.com"],
    ["principalSet://iam.googleapis.com/locations/global/workforcePools/p/*"],
    ["console-viewers@example.com"],
    ["group:console-viewers@example.com", "group:console-viewers@example.com"],
])
def test_iap_accessors_rejected(tmp_path: pathlib.Path, accessors: list[str]):
    res = _validate_vars(tmp_path, {"iap_accessors": accessors})
    assert res.returncode != 0, f"{accessors} should be rejected"
    assert "iap_accessors" in res.stdout + res.stderr
