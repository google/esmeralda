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

"""Agent Identity trust domain: one definition, organization from the project ancestry.

For a project whose parent is a folder, the google_project data source returns an empty org_id. A
trust domain built from it fell back to agents.global.proj-<number>.system.id.goog, a pool that does
not exist for a project in an organization, and every Agent Identity IAM binding failed.
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
MODULES = REPO_ROOT / "infrastructure/modules"
SHARED = MODULES / "_shared/agent_identity_trust_domain"
RESOLVE_TF = SHARED / "resolve/main.tf"

# Every module that builds an Agent Identity principal.
CALLERS = {
    MODULES / "3-security/agent_identity.tf": "../_shared/agent_identity_trust_domain",
    MODULES / "4-governance/modules/6_agent_gateway/main.tf": "../../../_shared/agent_identity_trust_domain",
    MODULES / "5-workloads/agents/a2a-agent/main.tf": "../../../_shared/agent_identity_trust_domain",
    MODULES / "5-workloads/agents/adk-agent/main.tf": "../../../_shared/agent_identity_trust_domain",
}


def _tf_files() -> list[pathlib.Path]:
    return sorted(p for p in MODULES.rglob("*.tf") if ".terraform" not in p.parts)


def _code(path: pathlib.Path) -> str:
    """The file without comment lines."""
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith(("#", "//")))


# --- Guards -----------------------------------------------------------------------------------------


# A trust domain built by interpolation: agents.global.${...}, agents.global.org-${...}, etc.
# Descriptions, error messages and validation regexes that only mention the format don't match.
_TRUST_DOMAIN_BUILDER = re.compile(r"agents\.global\.[^\"\s]*\$\{")


def test_trust_domain_is_built_in_one_place():
    """Only resolve/main.tf builds an agents.global.<scope>.system.id.goog trust domain."""
    builders = [p.relative_to(REPO_ROOT).as_posix() for p in _tf_files() if _TRUST_DOMAIN_BUILDER.search(_code(p))]
    assert builders == [RESOLVE_TF.relative_to(REPO_ROOT).as_posix()]
    assert len(_TRUST_DOMAIN_BUILDER.findall(_code(RESOLVE_TF))) == 1


def test_no_module_falls_back_to_a_project_scoped_pool():
    """No module outside resolve/ writes a proj-<number> trust domain."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in _tf_files()
        if p != RESOLVE_TF and re.search(r"proj-\$\{", _code(p))
    ]
    assert offenders == []


def test_no_module_reads_org_id_from_google_project():
    """google_project.org_id is empty for a project under a folder: only the shared module may read it."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in _tf_files()
        if SHARED not in p.parents
        and re.search(r"data\.google_project\.[a-z0-9_]+(\[[^\]]+\])?\.org_id|\bp\.org_id\b", _code(p))
    ]
    assert offenders == []


@pytest.mark.parametrize("path", sorted(CALLERS), ids=lambda p: p.relative_to(MODULES).as_posix())
def test_principal_builders_use_the_shared_module(path: pathlib.Path):
    assert f'source     = "{CALLERS[path]}"' in path.read_text()
    assert (path.parent / CALLERS[path]).resolve() == SHARED.resolve()


def test_shared_module_resolves_the_organization_of_a_folder():
    main = _code(SHARED / "main.tf")
    assert 'data "google_folder" "parent"' in main
    assert "lookup_organization = true" in main
    assert 'folder              = "folders/${data.google_project.this.folder_id}"' in main
    assert "folder_organization = try(data.google_folder.parent[0].organization, \"\")" in main
    assert "project_folder_id   = data.google_project.this.folder_id" in main


# --- Resolution logic (terraform apply on resolve/main.tf, no provider) -------------------------------


def _tf_binary() -> str:
    if os.environ.get("TF_BIN") and shutil.which(os.environ["TF_BIN"]):
        return shutil.which(os.environ["TF_BIN"])  # type: ignore[return-value]
    for candidate in (os.fspath(pathlib.Path.home() / ".terraform/bin/terraform"), "terraform", "tofu"):
        if found := shutil.which(candidate):
            return found
    pytest.exit("tests/security requires `terraform` or `tofu` on PATH (or TF_BIN)", returncode=2)


def _resolve(workdir: pathlib.Path, tfvars: dict[str, Any]) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RESOLVE_TF, workdir / "main.tf")
    (workdir / "terraform.tfvars.json").write_text(json.dumps(tfvars))
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    tf = _tf_binary()
    init = subprocess.run([tf, f"-chdir={workdir}", "init", "-backend=false", "-input=false", "-no-color"],
                          capture_output=True, text=True, env=env)
    assert init.returncode == 0, init.stdout + init.stderr
    res = subprocess.run([tf, f"-chdir={workdir}", "apply", "-auto-approve", "-input=false", "-no-color"],
                         capture_output=True, text=True, env=env)
    if res.returncode != 0:
        return res, {}
    out = subprocess.run([tf, f"-chdir={workdir}", "output", "-json"], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return res, {k: v["value"] for k, v in json.loads(out.stdout).items()}


def test_project_directly_under_the_organization(tmp_path: pathlib.Path):
    res, out = _resolve(tmp_path, {"project_number": "111", "project_org_id": "1234567890"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert out["trust_domain"] == "agents.global.org-1234567890.system.id.goog"
    assert out["org_id"] == "1234567890"


def test_project_under_a_folder_uses_the_folder_organization(tmp_path: pathlib.Path):
    """The case of the bug: google_project.org_id is empty, the trust domain is still org-<ORG_ID>."""
    tfvars = {
        "project_number": "111",
        "project_org_id": "",
        "project_folder_id": "555",
        "folder_organization": "organizations/1234567890",
        "org_id": "999",
    }
    res, out = _resolve(tmp_path, tfvars)
    assert res.returncode == 0, res.stdout + res.stderr
    # The folder lookup wins over the optional org_id input.
    assert out["trust_domain"] == "agents.global.org-1234567890.system.id.goog"


def test_project_without_organization_keeps_the_project_scoped_pool(tmp_path: pathlib.Path):
    res, out = _resolve(tmp_path, {"project_number": "111", "org_id": "999"})
    assert res.returncode == 0, res.stdout + res.stderr
    # No folder and no organization: org_id is not applied, the pool is project-scoped.
    assert out["trust_domain"] == "agents.global.proj-111.system.id.goog"
    assert out["org_id"] == ""


def test_folder_without_resolvable_organization_uses_the_org_id_input(tmp_path: pathlib.Path):
    tfvars = {"project_number": "111", "project_folder_id": "555", "org_id": "organizations/42"}
    res, out = _resolve(tmp_path, tfvars)
    assert res.returncode == 0, res.stdout + res.stderr
    assert out["trust_domain"] == "agents.global.org-42.system.id.goog"


def test_folder_without_resolvable_organization_fails(tmp_path: pathlib.Path):
    """Never fall back to proj-<number> for a project in a folder."""
    res, _ = _resolve(tmp_path, {"project_number": "111", "project_folder_id": "555"})
    assert res.returncode != 0
    assert "is in folder 555, but the organization of that folder could not be resolved" in _text(res)


def test_non_numeric_organization_fails(tmp_path: pathlib.Path):
    res, _ = _resolve(tmp_path, {"project_number": "111", "project_org_id": "my-org"})
    assert res.returncode != 0
    assert 'organization ID "my-org" must be numeric' in _text(res)


def _text(res: subprocess.CompletedProcess[str]) -> str:
    """Terraform output with its line wrapping undone."""
    return " ".join((res.stdout + res.stderr).split())


# --- Shared module wiring (terraform test with a mocked google provider) ------------------------------


def test_shared_module_terraform_test():
    """tests/trust_domain.tftest.hcl: folder lookup only for a folder parent, outputs per case."""
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    tf = _tf_binary()
    init = subprocess.run([tf, f"-chdir={SHARED}", "init", "-backend=false", "-input=false", "-no-color"],
                          capture_output=True, text=True, env=env)
    assert init.returncode == 0, init.stdout + init.stderr
    res = subprocess.run([tf, f"-chdir={SHARED}", "test", "-no-color"], capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "0 failed" in res.stdout, res.stdout
