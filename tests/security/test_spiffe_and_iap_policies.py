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
import re
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

    # Agents call out with their own Agent Identity; SERVICE_ACCOUNT_EMAIL only with the deprecated fallback
    assert 'variable "legacy_shared_invoker"' in variables_tf
    assert 'variable "enable_native_spiffe_auth"' not in variables_tf
    assert "SERVICE_ACCOUNT_EMAIL           = var.legacy_shared_invoker &&" in main_tf


def test_layer_3_security_restricts_orchestrator_from_mcp_project_egress():
    """Layer 3 security must not grant cx-mortgage-orchestrator roles/iap.egressor on mcps_project_id."""
    content = SECURITY_AGENT_ID_TF.read_text()
    # No agent principalSet holds project-level run.invoker on the MCPs project (see test_agent_impersonation_scope.py).
    assert 'resource "google_project_iam_member" "ai_coe_mortgage_specialist_identity_mcp_run_invoker"' not in content

    # Extract the cx_mortgage_orchestrator_iap_egressor resource block
    marker = 'resource "google_project_iam_member" "cx_mortgage_orchestrator_iap_egressor"'
    assert marker in content
    block = content.split(marker, 1)[1].split("\n}", 1)[0]
    assert "var.ai_coe_agents_project_id" in block
    assert "var.mcps_project_id" not in block, (
        "cx_mortgage_orchestrator_iap_egressor must not include var.mcps_project_id"
    )


@pytest.mark.parametrize("tg_path", [DEV_IAP_TG, PRD_IAP_TG], ids=["dev", "prd"])
def test_iap_egress_terragrunt_reads_per_target_callers_from_the_catalog(tg_path: pathlib.Path):
    """iap-egress units take the per-target lists from the compiled catalog, never hand-written."""
    content = tg_path.read_text()
    code = "\n".join(line.split("#", 1)[0] for line in content.splitlines())

    # The registry root is layer 4's alone (and it only ever clears it): no root members here.
    assert "iap_egress_members" not in code
    assert re.search(r"^\s*target_iap_members\s*=\s*local\.env_vars\.locals\.catalog_iap_egress\s*$", code, re.M)
    assert re.search(r"^\s*external_targets\s*=\s*local\.env_vars\.locals\.catalog_external_targets\s*$", code, re.M)
    assert re.search(r"^\s*domain\s*=\s*dependency\.networking\.outputs\.entrypoint_domain\s*$", code, re.M)
    # No hand-written per-target member list left in the unit.
    for target in ("ai-coe-mortgage-specialist", "corporate-email", "income-verification", "legacy-dms"):
        assert f'"{target}" = [' not in code, f"{tg_path}: hand-written IAP list for {target}"

    # identity_refs maps the refs: layer-3 service accounts generically, each agent's principal by name.
    refs = code.split("identity_refs = merge(", 1)[1]
    assert '"layer-3-security:${k}" => v' in refs and "dependency.security.outputs" in refs
    for agent in ("cx-mortgage-orchestrator", "ai-coe-mortgage-specialist"):
        dep = agent.replace("-", "_")
        assert (
            f'"agents/{agent}:agent_identity_principal"' in refs
            and f"dependency.{dep}.outputs.agent_identity_principal" in refs
        ), agent


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_compiled_iap_lists_enforce_least_privilege_per_target_callers(env: str):
    """Specialist entry: orchestrator (+ test VM), never itself. MCP entries: specialist, never the orchestrator."""
    iap = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())["iap_egress"]

    spec = iap["agents"]["ai-coe-mortgage-specialist"]
    assert "agents/cx-mortgage-orchestrator:agent_identity_principal" in spec
    # Agents call out with their own Agent Identity: no agent service account, no shared invoker.
    assert not any("_sa_email" in m and "test_vm" not in m for m in spec)
    assert not any("ai_coe_mortgage_specialist" in m or "ai-coe-mortgage-specialist" in m for m in spec)

    assert set(iap["mcp_servers"]) == {"corporate-email", "income-verification", "legacy-dms"}
    for mcp_id, members in iap["mcp_servers"].items():
        assert "agents/ai-coe-mortgage-specialist:agent_identity_principal" in members, mcp_id
        assert not any("mcp_invoker" in m or "ai_coe_mortgage_specialist_sa_email" in m for m in members), mcp_id
        assert not any("cx_mortgage_orchestrator" in m or "cx-mortgage-orchestrator" in m for m in members), mcp_id


def test_iap_egress_module_resolves_refs_and_fails_closed_on_unmapped_ones():
    main_tf = (IAP_EGRESS_MOD_DIR / "main.tf").read_text()
    variables_tf = (IAP_EGRESS_MOD_DIR / "variables.tf").read_text()
    assert 'variable "identity_refs"' in variables_tf
    assert "lookup(var.identity_refs, m, m)" in main_tf
    assert "condition     = length(local.unresolved_refs) == 0" in main_tf


@pytest.mark.parametrize(
    ("tf_path", "mode"),
    [(AGW_MAIN_TF, "system-endpoints"), (IAP_EGRESS_MOD_DIR / "main.tf", "targets")],
    ids=["layer4-agent-gateway", "layer5-iap-egress"],
)
def test_grant_script_callers_pass_their_mode(tf_path: pathlib.Path, mode: str):
    """Layer 4 writes the system endpoint entries, layer 5 the per-target entries, through the same script."""
    content = tf_path.read_text()
    assert "grant_iap_egress.sh" in content
    assert f'IAP_MODE                 = "{mode}"' in content
    assert "GOVERNANCE_PROJECT_ID" in content and "REGION" in content
    # The script path must resolve from the module directory to modules/_shared/scripts.
    rel = content.split('command = "bash ${path.module}/', 1)[1].split('"', 1)[0]
    assert (tf_path.parent / rel).resolve() == GRANT_IAP_SCRIPT.resolve()


def test_layer4_passes_every_system_endpoint_to_the_script():
    content = AGW_MAIN_TF.read_text()
    assert "IAP_MEMBERS_JSON         = jsonencode(local.iap_egress_members)" in content
    assert "SYSTEM_ENDPOINT_IDS_JSON = jsonencode(sort(keys(local.system_endpoints)))" in content
    assert "depends_on = [google_agent_registry_service.system_endpoints]" in content


def test_layer4_root_iap_members_exclude_service_account_emails():
    """The system endpoint IAP members are agent principals, not the agent/test VM SA emails (CA secret readers)."""
    content = AGW_MAIN_TF.read_text()
    locals_block = content.split("dynamic_agent_members = flatten([", 1)[1].split("\n}", 1)[0]
    assert "var.agent_invoker_sa_emails" not in locals_block


def test_no_root_level_iap_egress_binding_is_ever_written_again():
    """The script has no code path that writes a registry-root binding, and no root condition is left."""
    script = GRANT_IAP_SCRIPT.read_text()
    assert "startsWith" not in script and "condition: {" not in script
    assert "set_root_policy" not in script
    # The only root write clears the policy, and is guarded.
    assert '\'{"bindings": []}\'' in script
    assert "Refusing to write a registry-root IAP binding" in script
    for tf in (AGW_MAIN_TF, IAP_EGRESS_MOD_DIR / "main.tf", IAP_EGRESS_MOD_DIR / "external_targets.tf"):
        text = tf.read_text()
        assert "GOVERNANCE_PROJECT_NUMBER" not in text, tf
        assert "iap_web/agentRegistry/endpoints/" not in text, tf


def test_external_entries_wait_for_a_closed_registry_root():
    text = (IAP_EGRESS_MOD_DIR / "external_targets.tf").read_text()
    assert 'IAP_MODE              = "check-root"' in text
    assert "depends_on = [null_resource.require_closed_registry_root]" in text
    main_tf = (IAP_EGRESS_MOD_DIR / "main.tf").read_text()
    assert "depends_on = [google_agent_registry_service.external]" in main_tf


# ------------------------------------------------------------------------------------------
# grant_iap_egress.sh against a fake gcloud / curl
# ------------------------------------------------------------------------------------------

AGENT_SET = "principalSet://agents.global.org-123456.system.id.goog/attribute.platformContainer/aiplatform/projects/111"
CONTAINER_SET = "principalSet://agents.global.org-123456.system.id.goog/attribute.container/projects/111"
ORCH = "principal://agents.global.org-123456.system.id.goog/resources/aiplatform/projects/111/locations/us-central1/reasoningEngines/orch-1"
SPEC = "principal://agents.global.org-123456.system.id.goog/resources/aiplatform/projects/222/locations/us-central1/reasoningEngines/spec-1"
OLD_ROOT = {
    "version": 3,
    "bindings": [{
        "role": "roles/iap.egressor",
        "members": [AGENT_SET],
        "condition": {"title": "system-endpoints-only", "expression": 'resource.name.startsWith("x")'},
    }],
}

# service_id -> (collection, uid)
REGISTRY = {
    "telemetry": ("endpoints", "agentregistry-0001"),
    "aiplatform": ("endpoints", "agentregistry-0002"),
    "external-probe": ("endpoints", "agentregistry-0003"),
    "legacy-dms": ("mcpServers", "agentregistry-0004"),
    "ai-coe-mortgage-specialist": ("agents", "agentregistry-0005"),
}


class FakeCloud:
    """A fake gcloud + curl on PATH: a registry listing and IAP policies kept in a state directory."""

    def __init__(self, tmp_path: pathlib.Path, *, root_policy: dict | None = None,
                 registry: dict | None = None, fail_set_on: str | None = None,
                 quota_errors: int = 0):
        self.dir = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.state = tmp_path / "policies.json"
        self.calls = tmp_path / "calls.jsonl"
        self.state.write_text(json.dumps({"root": root_policy or {"etag": "ACAB"}}))
        services = [
            {"name": f"projects/esm-dev-governance/locations/{REGION}/services/{sid}",
             "registryResource": f"projects/{GOVERNANCE_PROJECT_NUMBER}/locations/{REGION}/{coll}/{uid}"}
            for sid, (coll, uid) in (registry or REGISTRY).items()
        ]
        (tmp_path / "services.json").write_text(json.dumps({"services": services}))
        gcloud = self.bin / "gcloud"
        (tmp_path / "quota.json").write_text(json.dumps({"left": quota_errors}))
        gcloud.write_text(f"""#!/usr/bin/env python3
import fcntl, json, pathlib, sys
state_path = pathlib.Path({str(self.state)!r})
quota_path = pathlib.Path({str(tmp_path / "quota.json")!r})
args = sys.argv[1:]
if args[:2] == ["auth", "print-access-token"]:
    print("fake-token"); sys.exit(0)
def key():
    flags = [a for a in args if a.startswith(("--agent=", "--endpoint=", "--mcp-server="))]
    return flags[0] if flags else "root"
lock = open({str(tmp_path / "gcloud.lock")!r}, "w")
fcntl.flock(lock, fcntl.LOCK_EX)  # parallel workers: one fake call at a time
state = json.loads(state_path.read_text())
if args[:3] == ["iap", "web", "get-iam-policy"]:
    print(json.dumps(state.get(key(), {{"etag": "ACAB"}}))); sys.exit(0)
if args[:3] == ["iap", "web", "set-iam-policy"]:
    policy = json.loads(pathlib.Path(args[3]).read_text())
    if {fail_set_on!r} and key() == {fail_set_on!r}:
        sys.exit(1)
    quota = json.loads(quota_path.read_text())
    if quota["left"] > 0:
        quota["left"] -= 1
        quota_path.write_text(json.dumps(quota))
        print("ERROR: (gcloud.iap.web.set-iam-policy) RESOURCE_EXHAUSTED: Quota exceeded", file=sys.stderr)
        sys.exit(1)
    with open({str(self.calls)!r}, "a") as f:
        f.write(json.dumps({{"key": key(), "policy": policy}}) + "\\n")
    state[key()] = policy
    state_path.write_text(json.dumps(state)); sys.exit(0)
sys.exit(2)
""")
        curl = self.bin / "curl"
        curl.write_text(f"""#!/usr/bin/env python3
import shutil, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
shutil.copy({str(tmp_path / "services.json")!r}, out)
""")
        for exe in (gcloud, curl):
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def run(self, env: dict[str, str | None], *, check: bool = True) -> subprocess.CompletedProcess:
        full = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "GOVERNANCE_PROJECT_ID": "esm-dev-governance",
            "REGION": REGION,
            "IAP_RETRY_DELAY_SECONDS": "0",
            "IAP_MAX_ATTEMPTS": "2",
            "IAP_WRITE_PAUSE_SECONDS": "0",
        }
        for k, v in env.items():
            if v is None:
                full.pop(k, None)
            else:
                full[k] = v
        res = subprocess.run(["bash", str(GRANT_IAP_SCRIPT)], env=full, capture_output=True, text=True)
        if check:
            assert res.returncode == 0, res.stderr + res.stdout
        return res

    @property
    def writes(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines() if line.strip()]

    @property
    def policies(self) -> dict:
        return json.loads(self.state.read_text())


def _binding(*members: str) -> dict:
    return {"bindings": [{"role": "roles/iap.egressor", "members": list(members)}]}


def test_system_endpoints_are_granted_and_verified_before_the_root_binding_is_cleared(tmp_path):
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT)
    cloud.run({
        "IAP_MODE": "system-endpoints",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET, CONTAINER_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(["telemetry", "aiplatform"]),
    })
    keys = [w["key"] for w in cloud.writes]
    # Every system endpoint first (in any order: parallel workers), the root last (no gap: the old
    # root grant stays until then).
    assert sorted(keys[:-1]) == ["--endpoint=agentregistry-0001", "--endpoint=agentregistry-0002"]
    assert keys[-1] == "root"
    assert all(w["policy"] == _binding(AGENT_SET, CONTAINER_SET) for w in cloud.writes[:-1])
    assert cloud.writes[-1]["policy"] == {"bindings": []}
    # Only the system endpoints: the external endpoint, agents and MCP servers are untouched.
    assert "--endpoint=agentregistry-0003" not in cloud.policies


def test_system_endpoint_grants_are_idempotent(tmp_path):
    cloud = FakeCloud(tmp_path)
    env = {
        "IAP_MODE": "system-endpoints",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(["telemetry", "aiplatform"]),
    }
    cloud.run(env)
    first = len(cloud.writes)
    res = cloud.run(env)
    assert len(cloud.writes) == first  # nothing rewritten, root already clear
    assert "unchanged" in res.stdout


def test_root_is_left_alone_when_a_system_endpoint_grant_fails(tmp_path):
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT, fail_set_on="--endpoint=agentregistry-0002")
    res = cloud.run({
        "IAP_MODE": "system-endpoints",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(["telemetry", "aiplatform"]),
    }, check=False)
    assert res.returncode != 0
    assert cloud.policies["root"] == OLD_ROOT
    assert all(w["key"] != "root" for w in cloud.writes)


def test_system_endpoint_mode_fails_closed_on_an_unregistered_entry(tmp_path):
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT)
    res = cloud.run({
        "IAP_MODE": "system-endpoints",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(["telemetry", "not-registered"]),
    }, check=False)
    assert res.returncode != 0 and "not-registered is not registered" in res.stderr
    assert cloud.writes == [] and cloud.policies["root"] == OLD_ROOT


def test_per_target_policies_cover_agents_endpoints_and_mcp_servers(tmp_path):
    cloud = FakeCloud(tmp_path)
    cloud.run({
        "IAP_MODE": "targets",
        "IAP_TARGET_POLICIES_JSON": json.dumps({
            "agents": {"ai-coe-mortgage-specialist": [ORCH]},
            "endpoints": {"external-probe": [SPEC]},
            "mcp_servers": {"legacy-dms": [SPEC]},
        }),
    })
    by_key = {w["key"]: w["policy"] for w in cloud.writes}
    assert by_key == {
        "--agent=agentregistry-0005": _binding(ORCH),
        "--endpoint=agentregistry-0003": _binding(SPEC),
        "--mcp-server=agentregistry-0004": _binding(SPEC),
    }
    # Per-target mode never touches the registry root or the system endpoints.
    assert "root" not in by_key and "--endpoint=agentregistry-0001" not in by_key


def test_target_with_no_callers_gets_an_empty_policy(tmp_path):
    """A managed: false target with no enforced callers grants nothing: an explicit empty policy."""
    cloud = FakeCloud(tmp_path)
    cloud.run({
        "IAP_MODE": "targets",
        "IAP_TARGET_POLICIES_JSON": json.dumps({"endpoints": {"external-probe": [SPEC]}}),
    })
    cloud.run({
        "IAP_MODE": "targets",
        "IAP_TARGET_POLICIES_JSON": json.dumps({"endpoints": {"external-probe": []}}),
    })
    assert cloud.policies["--endpoint=agentregistry-0003"] == {"bindings": []}


def test_endpoint_grants_refuse_while_the_root_still_has_a_binding(tmp_path):
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT)
    res = cloud.run({
        "IAP_MODE": "targets",
        "IAP_TARGET_POLICIES_JSON": json.dumps({"endpoints": {"external-probe": [SPEC]}}),
    }, check=False)
    assert res.returncode != 0 and "Apply layer 4 governance first" in res.stderr
    assert cloud.writes == []
    # Agents and MCP servers aren't covered by the old endpoints-only root condition: still granted.
    cloud.run({
        "IAP_MODE": "targets",
        "IAP_TARGET_POLICIES_JSON": json.dumps({"mcp_servers": {"legacy-dms": [SPEC]}}),
    })
    assert [w["key"] for w in cloud.writes] == ["--mcp-server=agentregistry-0004"]


def test_target_of_the_wrong_entry_type_fails(tmp_path):
    cloud = FakeCloud(tmp_path)
    res = cloud.run({
        "IAP_MODE": "targets",
        "IAP_TARGET_POLICIES_JSON": json.dumps({"endpoints": {"legacy-dms": [SPEC]}}),
    }, check=False)
    assert res.returncode != 0 and "expected endpoints" in res.stderr
    assert cloud.writes == []


@pytest.mark.parametrize(("root_policy", "ok"), [(None, True), (OLD_ROOT, False)], ids=["closed", "open"])
def test_check_root_mode(tmp_path, root_policy, ok):
    cloud = FakeCloud(tmp_path, root_policy=root_policy)
    res = cloud.run({"IAP_MODE": "check-root"}, check=False)
    assert (res.returncode == 0) is ok
    assert cloud.writes == []


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"IAP_MODE": None}, "IAP_MODE"),
        ({"IAP_MODE": "root"}, "IAP_MODE must be"),
        ({"GOVERNANCE_PROJECT_ID": "bad id; rm -rf /"}, "GOVERNANCE_PROJECT_ID must be a project id"),
        ({"REGION": 'us-central1") || true || ("'}, "REGION"),
    ],
    ids=["missing-mode", "unknown-mode", "project-injection", "region-injection"],
)
def test_grant_script_rejects_bad_inputs(tmp_path, override, message):
    cloud = FakeCloud(tmp_path)
    res = cloud.run({"IAP_MODE": "check-root", **override}, check=False)
    assert res.returncode != 0
    assert message in res.stderr
    assert cloud.writes == []


def _many_endpoints(n: int) -> dict:
    return {f"google-api-{i:03d}": ("endpoints", f"agentregistry-{i:04d}") for i in range(n)}


def test_many_system_endpoints_run_in_parallel_and_all_verify_before_the_root_clear(tmp_path):
    registry = _many_endpoints(30)
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT, registry=registry)
    res = cloud.run({
        "IAP_MODE": "system-endpoints",
        "IAP_CONCURRENCY": "6",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(sorted(registry)),
    })
    keys = [w["key"] for w in cloud.writes]
    assert len(keys) == 31 and keys[-1] == "root" and len(set(keys[:-1])) == 30
    assert "30 entries written (where needed) and verified" in res.stdout
    assert all(cloud.policies[f"--endpoint={uid}"] == _binding(AGENT_SET) for _, uid in registry.values())


def test_quota_errors_are_retried_with_backoff(tmp_path):
    """RESOURCE_EXHAUSTED (429) answers are retried; the run still completes and verifies."""
    registry = _many_endpoints(5)
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT, registry=registry, quota_errors=3)
    res = cloud.run({
        "IAP_MODE": "system-endpoints",
        "IAP_CONCURRENCY": "3",
        "IAP_MAX_ATTEMPTS": "6",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(sorted(registry)),
    })
    assert "retrying" in res.stderr
    assert cloud.policies["root"] == {"bindings": []}
    assert all(cloud.policies[f"--endpoint={uid}"] == _binding(AGENT_SET) for _, uid in registry.values())


def test_one_failed_entry_among_many_leaves_the_root_untouched(tmp_path):
    registry = _many_endpoints(12)
    cloud = FakeCloud(tmp_path, root_policy=OLD_ROOT, registry=registry, fail_set_on="--endpoint=agentregistry-0007")
    res = cloud.run({
        "IAP_MODE": "system-endpoints",
        "IAP_CONCURRENCY": "4",
        "IAP_MEMBERS_JSON": json.dumps([AGENT_SET]),
        "SYSTEM_ENDPOINT_IDS_JSON": json.dumps(sorted(registry)),
    }, check=False)
    assert res.returncode != 0 and "1 of 12 entries could not be written and verified" in res.stderr
    assert cloud.policies["root"] == OLD_ROOT
    assert all(w["key"] != "root" for w in cloud.writes)


@pytest.mark.parametrize("value", ["0", "abc", "100"])
def test_bad_concurrency_is_rejected(tmp_path, value):
    cloud = FakeCloud(tmp_path)
    res = cloud.run({"IAP_MODE": "check-root", "IAP_CONCURRENCY": value}, check=False)
    assert res.returncode != 0 and "IAP_CONCURRENCY" in res.stderr
