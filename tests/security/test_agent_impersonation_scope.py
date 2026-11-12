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

"""Static IAM guard: an agent identity can only mint tokens for the identities it is meant to call out as.

A project-level ``roles/iam.serviceAccountTokenCreator`` (or a role that contains it) on an agent identity
lets that agent impersonate every service account of the project, including ones that hold invoke rights
the catalog never gave the agent (for example the test VM service account, which can invoke the MCP
services). The same goes for project-level ``roles/run.invoker`` on the MCPs project: every engine of the
agent project could then call every MCP service, whatever the catalog callers say.

These tests read the Terraform sources (no provider, no state).
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES = REPO_ROOT / "infrastructure/modules"
SECURITY_DIR = MODULES / "3-security"
ENVS = ("dev", "prd")

# Members that are an agent identity (Agent Runtime principal or principalSet), however they are written.
AGENT_MEMBER = re.compile(r"principalSet://|principal://|_identity_principal\b|agent_identity_principal\b|agent_principal\b")
# Roles that let the holder mint tokens for (or act fully as) other service accounts of the resource.
TOKEN_MINTING_ROLES = (
    "roles/iam.serviceAccountTokenCreator",
    "roles/iam.workloadIdentityUser",
    "roles/iam.serviceAccountKeyAdmin",
    "roles/owner",
    "roles/editor",
)
# Resource-hierarchy IAM: a grant here reaches every service account below it.
HIERARCHY_IAM_TYPES = (
    "google_project_iam_member",
    "google_project_iam_binding",
    "google_folder_iam_member",
    "google_folder_iam_binding",
    "google_organization_iam_member",
    "google_organization_iam_binding",
)
SA_IAM_TYPES = ("google_service_account_iam_member", "google_service_account_iam_binding")
# The only service account agent identities may mint tokens for until per-agent outbound identities
# land: the shared invoker every agent calls out as while enable_native_spiffe_auth = false.
ALLOWED_IMPERSONATION_TARGETS = {"google_service_account.mcp_invoker_sa.name"}


def _strip_comments(text: str) -> str:
    """Drops `#` comments (whole-line or trailing after whitespace); `//` is left alone (principal:// URIs)."""
    return "\n".join(re.split(r"(?:^|\s)#", line, maxsplit=1)[0] for line in text.splitlines())


def _resources(rtype: str) -> list[tuple[str, str, str]]:
    """(file, name, body) of every ``resource "<rtype>" "<name>" { ... }`` under modules/ (brace-balanced)."""
    found = []
    for path in sorted(MODULES.rglob("*.tf")):
        if ".terraform" in path.parts:
            continue
        text = _strip_comments(path.read_text())
        for match in re.finditer(rf'resource\s+"{rtype}"\s+"([^"]+)"\s*\{{', text):
            depth, i = 1, match.end()
            while depth and i < len(text):
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            found.append((str(path.relative_to(REPO_ROOT)), match.group(1), text[match.end():i - 1]))
    return found


def test_guard_finds_the_resources_it_checks():
    """The scanner must see the agent identity grants, or every other test here passes vacuously."""
    project_grants = [r for r in _resources("google_project_iam_member") if AGENT_MEMBER.search(r[2])]
    sa_grants = [r for r in _resources("google_service_account_iam_member") if AGENT_MEMBER.search(r[2])]
    assert len(project_grants) >= 2, project_grants
    assert len(sa_grants) >= 2, sa_grants


@pytest.mark.parametrize("rtype", HIERARCHY_IAM_TYPES)
def test_agent_identities_hold_no_hierarchy_level_token_minting_role(rtype: str):
    """No project/folder/org-level token-minting role for any agent identity (privilege escalation path)."""
    bad = [
        f"{path}: {name} ({role})"
        for path, name, body in _resources(rtype)
        if AGENT_MEMBER.search(body)
        for role in TOKEN_MINTING_ROLES
        if f'"{role}"' in body
    ]
    assert not bad, "agent identities can impersonate every service account of a project/folder/org:\n" + "\n".join(bad)


@pytest.mark.parametrize("rtype", SA_IAM_TYPES)
def test_agent_identities_may_only_impersonate_the_shared_invoker(rtype: str):
    """SA-scoped token minting for agent identities targets only the identity agents call out as."""
    bad = []
    for path, name, body in _resources(rtype):
        if not AGENT_MEMBER.search(body) or not any(f'"{role}"' in body for role in TOKEN_MINTING_ROLES):
            continue
        target = re.search(r"service_account_id\s*=\s*([^\s]+)", body)
        if not target or target.group(1) not in ALLOWED_IMPERSONATION_TARGETS:
            bad.append(f"{path}: {name} -> {target.group(1) if target else '?'}")
    assert not bad, "agent identities may mint tokens for an unexpected service account:\n" + "\n".join(bad)


def test_agent_identities_hold_no_project_level_run_invoker_on_the_mcp_project():
    """MCP invoke rights come per service from the catalog callers, never project-wide for agents."""
    bad = [
        f"{path}: {name}"
        for rtype in ("google_project_iam_member", "google_project_iam_binding")
        for path, name, body in _resources(rtype)
        if '"roles/run.invoker"' in body and "var.mcps_project_id" in body and AGENT_MEMBER.search(body)
    ]
    assert not bad, "agent identities hold project-level run.invoker on the MCPs project:\n" + "\n".join(bad)


@pytest.mark.parametrize("env", ENVS)
def test_envoy_invokers_are_the_catalog_callers_of_its_hosts(env: str):
    """Envoy forwards with its own identity, so its invokers must be the callers of the hosts routed to it."""
    unit = REPO_ROOT / f"infrastructure/live/{env}/layer-5-workloads/gateways/envoy/terragrunt.hcl"
    code = _strip_comments(unit.read_text())
    assert re.search(r'lookup\(local\.gateways\.host_rules,\s*name,\s*local\.gateways\.default\)\s*==\s*"envoy"', code)
    block = code.split("invoker_service_accounts", 1)[1].split("\n\n", 1)[0]
    assert "local.envoy_callers" in block
    for hardcoded in ("cx_mortgage_orchestrator_sa_email", "ai_coe_mortgage_specialist_sa_email", "test_vm_sa_email"):
        assert hardcoded not in block, f"{env}: Envoy invokers hard-code {hardcoded}"
