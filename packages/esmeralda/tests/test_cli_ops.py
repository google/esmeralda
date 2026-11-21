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

"""Unit tests for the ``esmeralda`` operational CLI subcommands and ``esmeralda.ops``.

All forbidden/secret test strings are constructed dynamically at runtime so this test file
never contains literal forbidden strings.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from esmeralda import cli
from esmeralda.ops import drift, envfile, guard, logs, repo, runtime

ENGINE = "projects/demo-cx/locations/us-central1/reasoningEngines/123"
DIGEST = "us-central1-docker.pkg.dev/demo/repo/legacy-dms@sha256:" + "ab" * 32


@pytest.fixture(autouse=True)
def _no_user_env_file(tmp_path, monkeypatch):
    """Never read the developer's real ~/.config/esmeralda/env in tests."""
    monkeypatch.setenv(envfile.ENV_FILE_VAR, str(tmp_path / "no-such-env-file"))

ENV_YAML = """
locals {
  environment      = "dev"
  state_project    = ""
  state_bucket     = ""
  image_repository = "dev"
  container_tag    = "dev-latest"
  gateways = {
    enabled    = []
    default    = "direct"
    host_rules = {}
  }
}
"""


def _state(outputs: dict, resources: int = 1, image: str | None = None) -> dict:
    res = [
        {
            "mode": "managed",
            "type": "google_cloud_run_v2_service",
            "name": f"r{i}",
            "instances": [{"attributes": {"image": image}}] if image and i == 0 else [],
        }
        for i in range(resources)
    ]
    return {
        "version": 4,
        "outputs": {k: {"value": v, "type": "string", "sensitive": False} for k, v in outputs.items()},
        "resources": res,
    }


@pytest.fixture
def ops_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    live = tmp_path / "infrastructure" / "live"
    (live / "dev").mkdir(parents=True)
    (live / "root.hcl").write_text("# root\n")
    (live / "dev" / "env.yaml").write_text(ENV_YAML)
    (tmp_path / "uv.lock").write_text("version = 1\n")

    units_hcl = {
        "layer-1-projects": "",
        "layer-2-networking": 'dependency "projects" {\n  config_path = "../layer-1-projects"\n}\n',
        "layer-5-workloads/agents/ai-coe-mortgage-specialist": "",
        "layer-5-workloads/agents/cx-mortgage-orchestrator": "",
        "layer-5-workloads/entrypoint": (
            'dependency "projects" {\n  config_path = "../../layer-1-projects"\n}\n'
            'dependency "networking" {\n  config_path = "../../layer-2-networking"\n}\n'
        ),
        "layer-5-workloads/gateways/direct": "",
        "layer-5-workloads/gateways/envoy": (
            'exclude {\n  if = !contains(local.env_vars.locals.gateways.enabled, "envoy")\n  actions = ["all"]\n}\n'
        ),
        "layer-5-workloads/services/legacy-dms": "",
    }
    for u, hcl in units_hcl.items():
        udir = live / "dev" / u
        udir.mkdir(parents=True, exist_ok=True)
        (udir / "terragrunt.hcl").write_text(hcl)

    states = {
        "layer-1-projects": _state({"cx_agents_project_id": "demo-cx", "mcps_project_id": "demo-mcps"}, 2),
        "layer-2-networking": _state({"entrypoint_address": "10.0.1.3", "entrypoint_domain": "example.internal"}, 3),
        "layer-5-workloads/agents/ai-coe-mortgage-specialist": _state({"engine_id": ENGINE}, 1),
        "layer-5-workloads/agents/cx-mortgage-orchestrator": _state({"engine_id": ENGINE}, 2),
        "layer-5-workloads/entrypoint": _state({"ip_address": "10.0.1.3", "routing": {"default": "direct"}}, 2),
        "layer-5-workloads/gateways/direct": _state(
            {
                "backend_service_id": None,
                "routes": {"legacy-dms": {"host": "legacy-dms.example.internal", "probe": "mcp"}},
            },
            4,
        ),
        "layer-5-workloads/services/legacy-dms": _state(
            {"service_name": "legacy-dms-dev", "service_uri": "https://legacy-dms.run.app"},
            1,
            image=DIGEST,
        ),
    }
    for u, st in states.items():
        spath = live / ".local_states" / "dev" / u / "terraform.tfstate"
        spath.parent.mkdir(parents=True, exist_ok=True)
        spath.write_text(json.dumps(st))

    for agent_name, fw in (("ai-coe-mortgage-specialist", "a2a"), ("cx-mortgage-orchestrator", "google-adk")):
        adir = tmp_path / "apps" / "agents" / agent_name
        adir.mkdir(parents=True)
        extra = "agent_card:\n  name: specialist\n" if fw == "a2a" else ""
        (adir / "agent.yaml").write_text(f"name: {agent_name}\nframework: {fw}\n{extra}")

    doc = tmp_path / "docs" / "src" / "2-workloads-and-catalog" / "01-ingress-gateways.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        textwrap.dedent(
            """\
            # Gateways
            ### 1. The Gateway Contract
            Gateway outputs `backend_service_id` and `routes`.
            ### 2. The `gateways` Block in `env.yaml`
            Configure `enabled`, `default`, `host_rules`.
            ### 3. The Entry Point
            Entry point wires URL map and HTTPS proxy.
            """
        )
    )
    monkeypatch.setenv(repo.REPO_ENV_VAR, str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------------------------------
# 1. Argument parsing
# ------------------------------------------------------------------------------------------


def test_parser_subcommands_and_flags():
    parser = cli._parser()

    ns = parser.parse_args(["status", "--env", "prd", "--json", "--repo", "/tmp/repo"])
    assert ns.cmd == "status" and ns.env == "prd" and ns.json is True and ns.repo == "/tmp/repo"

    ns = parser.parse_args(["doctor", "--env", "dev", "--json"])
    assert ns.cmd == "doctor" and ns.env == "dev" and ns.json is True

    ns = parser.parse_args(["units", "--env", "dev", "--layer", "5", "--json"])
    assert ns.cmd == "units" and ns.layer == 5 and ns.json is True

    ns = parser.parse_args(["plan", "entrypoint", "--env", "dev"])
    assert ns.cmd == "plan" and ns.unit == "entrypoint" and ns.all_units is False

    ns = parser.parse_args(["plan", "--all", "--env", "dev", "--json"])
    assert ns.cmd == "plan" and ns.all_units is True and ns.json is True

    ns = parser.parse_args(["apply", "entrypoint", "--env", "dev", "--allow-destroy", "--json"])
    assert ns.cmd == "apply" and ns.unit == "entrypoint" and ns.allow_destroy is True and ns.json is True

    ns = parser.parse_args(["verify", "--env", "dev", "--probe", "legacy-dms", "--skip-query", "--json"])
    assert ns.cmd == "verify" and ns.probe == "legacy-dms" and ns.skip_query is True and ns.json is True

    ns = parser.parse_args(["drift", "--env", "prd", "--json"])
    assert ns.cmd == "drift" and ns.env == "prd" and ns.json is True

    ns = parser.parse_args(
        ["logs", "cx-mortgage-orchestrator", "--env", "dev", "--minutes", "15", "--severity", "ERROR", "--limit", "25"]
    )
    assert ns.cmd == "logs" and ns.target == "cx-mortgage-orchestrator"
    assert ns.minutes == 15 and ns.severity == "ERROR" and ns.limit == 25

    ns = parser.parse_args(["contracts", "--section", "gateway_adapter", "--json"])
    assert ns.cmd == "contracts" and ns.section == "gateway_adapter" and ns.json is True

    ns = parser.parse_args(["guard", "--diff", "origin/main...HEAD", "--json"])
    assert ns.cmd == "guard" and ns.diff == "origin/main...HEAD" and ns.json is True


# ------------------------------------------------------------------------------------------
# 2. status & units & contracts (human vs --json)
# ------------------------------------------------------------------------------------------


def test_cli_status_human_and_json(ops_checkout, capsys):
    assert cli.main(["status", "--env", "dev"]) == 0
    human = capsys.readouterr().out
    assert "Environment 'dev'" in human
    assert "legacy-dms" in human
    assert "container_tag" not in human  # human format uses tag=dev-latest
    assert "tag=dev-latest" in human

    assert cli.main(["status", "--env", "dev", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["env"] == "dev"
    assert "git" in data and "worktrees" in data["git"]
    assert "layer-5-workloads/services/legacy-dms" in data["local_state_units"]
    assert data["images"]["container_tag"] == "dev-latest"
    assert data["images"]["digests"]["layer-5-workloads/services/legacy-dms"] == [DIGEST]


def test_cli_units_layer_filter_dependencies_and_enabled(ops_checkout, capsys):
    assert cli.main(["units", "--env", "dev", "--layer", "5", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["layer"] == 5
    by_unit = {u["unit"]: u for u in data["units"]}
    assert "layer-1-projects" not in by_unit
    assert by_unit["layer-5-workloads/entrypoint"]["dependencies"] == ["layer-1-projects", "layer-2-networking"]
    assert by_unit["layer-5-workloads/gateways/direct"]["enabled"] is True
    assert by_unit["layer-5-workloads/gateways/envoy"]["enabled"] is False
    assert by_unit["layer-5-workloads/gateways/envoy"]["excluded"] is True
    assert by_unit["layer-5-workloads/gateways/envoy"]["local_state"] is False

    assert cli.main(["units", "--env", "dev", "--layer", "2"]) == 0
    human = capsys.readouterr().out
    assert "layer-2-networking" in human and "layer-1-projects" in human


def test_cli_contracts_sections_human_and_json(ops_checkout, capsys):
    assert cli.main(["contracts", "--json"]) == 0
    all_data = json.loads(capsys.readouterr().out)
    assert "Entry point wires URL map" in all_data["entrypoint"]
    assert "backend_service_id" in all_data["gateway_adapter"]
    assert "ai-coe-mortgage-specialist" in all_data["agent_card"]["a2a_agents"]

    for sec in ("entrypoint", "gateway_adapter", "agent_card", "env_yaml"):
        assert cli.main(["contracts", "--section", sec, "--json"]) == 0
        sec_data = json.loads(capsys.readouterr().out)
        assert sec_data["section"] == sec

    assert cli.main(["contracts", "--section", "entrypoint"]) == 0
    assert "=== entrypoint ===" in capsys.readouterr().out


def _drift_result(*, passed: bool) -> dict:
    checks = [] if passed else [
        {"check": "entry legacy-dms", "ok": False, "detail": "manual member added", "source": "iap-egress"}
    ]
    return {
        "env": "dev",
        "passed": passed,
        "sources": ["iap-egress"],
        "ok_count": 3 if passed else 2,
        "failed": 0 if passed else 1,
        "checks": checks,
        "seconds": 0.4,
    }


def test_cli_drift_clean_prints_nothing(ops_checkout, monkeypatch, capsys):
    monkeypatch.setattr(drift, "check", lambda root, env: _drift_result(passed=True))

    assert cli.main(["drift", "--env", "dev"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_drift_reports_findings_and_exits_one(ops_checkout, monkeypatch, capsys):
    monkeypatch.setattr(drift, "check", lambda root, env: _drift_result(passed=False))

    assert cli.main(["drift", "--env", "dev"]) == 1
    out = capsys.readouterr().out
    assert "1 drift finding(s) for 'dev'" in out
    assert "[iap-egress] entry legacy-dms: manual member added" in out


def test_cli_drift_json_and_read_error(ops_checkout, monkeypatch, capsys):
    monkeypatch.setattr(drift, "check", lambda root, env: _drift_result(passed=False))

    assert cli.main(["drift", "--env", "dev", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is False and data["failed"] == 1

    def fail(root, env):
        raise repo.OpsError("cannot read registry policy")

    monkeypatch.setattr(drift, "check", fail)
    assert cli.main(["drift", "--env", "dev"]) == 2
    assert "cannot read registry policy" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------
# 3. plan & apply destroy guard
# ------------------------------------------------------------------------------------------

SAFE_PLAN = {
    "format_version": "1.2",
    "resource_changes": [
        {"address": "google_compute_url_map.ep", "change": {"actions": ["create"], "before": None, "after": {"x": 1}}},
        {
            "address": "google_compute_global_address.ip",
            "change": {"actions": ["update"], "before": {"a": 1}, "after": {"a": 2}},
        },
    ],
}

DESTRUCTIVE_PLAN = {
    "format_version": "1.2",
    "resource_changes": [
        {"address": "google_compute_url_map.ep", "change": {"actions": ["delete"], "before": {"x": 1}, "after": None}},
    ],
}

REPLACE_PLAN = {
    "format_version": "1.2",
    "resource_changes": [
        {
            "address": "google_cloud_run_v2_service.gw",
            "change": {
                "actions": ["delete", "create"],
                "before": {"location": "us-central1"},
                "after": {"location": "us-east1"},
                "replace_paths": [["location"]],
            },
        },
    ],
}


def _mock_terragrunt(monkeypatch: pytest.MonkeyPatch, plan_doc: dict) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path, timeout: float, what: str):
        calls.append(cmd)
        if "plan" in cmd:
            for arg in cmd:
                if arg.startswith("-out="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"tfplan")
            return subprocess.CompletedProcess(cmd, 0, "Plan: 1 to add, 0 to change, 0 to destroy.", "")
        if "show" in cmd:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(plan_doc), "")
        if "apply" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "Apply complete!", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(repo, "run", fake_run)
    return calls


def test_cli_plan_short_unit_name_and_all(ops_checkout, monkeypatch, capsys):
    _mock_terragrunt(monkeypatch, SAFE_PLAN)

    assert cli.main(["plan", "entrypoint", "--env", "dev"]) == 0
    out = capsys.readouterr().out
    assert "Plan dev/layer-5-workloads/entrypoint: 1 to add, 1 to change, 0 to destroy" in out

    assert cli.main(["plan", "--all", "--env", "dev", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["all"] is True
    # envoy is excluded in env.yaml, so 7 of 8 units are planned
    assert len(data["units"]) == 7
    assert data["counts"] == {"add": 7, "change": 7}


def test_cli_apply_refuses_destroy_without_allow_destroy(ops_checkout, monkeypatch, capsys):
    calls = _mock_terragrunt(monkeypatch, DESTRUCTIVE_PLAN)

    rc = cli.main(["apply", "entrypoint", "--env", "dev"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "Refusing to apply dev/layer-5-workloads/entrypoint" in out
    assert "--allow-destroy" in out
    assert not any("apply" in cmd for cmd in calls)

    calls.clear()
    rc_json = cli.main(["apply", "entrypoint", "--env", "dev", "--json"])
    assert rc_json == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False and payload["refused"] is True and payload["exit_code"] == 2
    assert payload["destroy_count"] == 1
    assert not any("apply" in cmd for cmd in calls)


def test_cli_apply_refuses_replace_without_allow_destroy_and_applies_with_flag(ops_checkout, monkeypatch, capsys):
    calls = _mock_terragrunt(monkeypatch, REPLACE_PLAN)

    assert cli.main(["apply", "direct", "--env", "dev"]) == 2
    capsys.readouterr()
    assert not any("apply" in cmd for cmd in calls)

    calls.clear()
    assert cli.main(["apply", "direct", "--env", "dev", "--allow-destroy", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True and payload["refused"] is False and payload["exit_code"] == 0
    assert any("apply" in cmd for cmd in calls)


def test_cli_apply_succeeds_on_safe_plan_without_flag(ops_checkout, monkeypatch, capsys):
    calls = _mock_terragrunt(monkeypatch, SAFE_PLAN)
    assert cli.main(["apply", "entrypoint", "--env", "dev"]) == 0
    out = capsys.readouterr().out
    assert "Applied dev/layer-5-workloads/entrypoint" in out
    assert any("apply" in cmd for cmd in calls)


def test_cli_apply_timeout_defaults_to_900s_and_is_configurable(ops_checkout, monkeypatch, capsys):
    """Long provisioners (per-entry IAP grants) need more than the default; the flag reaches plan and apply."""
    _mock_terragrunt(monkeypatch, SAFE_PLAN)
    seen: dict[str, float] = {}
    real_run = repo.run

    def recording_run(cmd, **kwargs):
        for verb in ("plan", "apply"):
            if verb in cmd:
                seen[verb] = kwargs.get("timeout")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(repo, "run", recording_run)
    assert cli.main(["apply", "entrypoint", "--env", "dev"]) == 0
    assert seen == {"plan": 900.0, "apply": 900.0}
    seen.clear()
    assert cli.main(["apply", "entrypoint", "--env", "dev", "--timeout", "2400"]) == 0
    assert seen == {"plan": 2400.0, "apply": 2400.0}
    capsys.readouterr()
    for bad in ("0", "-5", "soon"):
        assert cli.main(["apply", "entrypoint", "--env", "dev", "--timeout", bad]) != 0
        assert "--timeout" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------
# 4. doctor
# ------------------------------------------------------------------------------------------


def _fake_version(binary, args, cwd):
    return "3.12.3" if binary.endswith("python3") else "1.9.0"


def test_cli_doctor_fails_on_too_old_python_or_terraform(ops_checkout, monkeypatch, capsys):
    versions = {"python3": "3.9.18", "terraform": "1.4.6"}
    monkeypatch.setattr(repo, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_which_outside_venv", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_tool_version", lambda binary, args, cwd: versions.get(binary.rsplit("/", 1)[-1], "2.0"))
    monkeypatch.setattr(repo, "_check_adc", lambda root: (True, "ADC token available"))
    monkeypatch.setattr(repo, "_check_uv_lock", lambda root: (True, "ok"))

    assert cli.main(["doctor", "--env", "dev", "--json"]) == 1
    by_name = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert by_name["tool:python3"]["status"] == "fail" and "needs >= 3.10" in by_name["tool:python3"]["detail"]
    assert by_name["tool:terraform"]["status"] == "fail" and "needs >= 1.5" in by_name["tool:terraform"]["detail"]

    versions.update(python3="3.10.0", terraform="installed")  # unparseable versions are not judged
    assert cli.main(["doctor", "--env", "dev", "--json"]) == 0


def test_below_minimum():
    assert repo._below_minimum("python3", "3.9") == "needs >= 3.10"
    assert repo._below_minimum("python3", "3.10.1") == ""
    assert repo._below_minimum("terraform", "1.14.0") == ""
    assert repo._below_minimum("jq", "1.0") == ""


def test_python_check_ignores_the_active_venv(tmp_path, monkeypatch):
    """`uv run` puts the workspace venv first on PATH; doctor judges the workstation's python3."""
    venv, system = tmp_path / "venv", tmp_path / "system"
    for d in (venv / "bin", system):
        d.mkdir(parents=True)
        (d / "python3").write_text("#!/bin/sh\n")
        (d / "python3").chmod(0o755)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setattr(repo, "tool_env", lambda: {"PATH": f"{venv / 'bin'}:{system}"})
    assert repo._which_outside_venv("python3") == str(system / "python3")


def test_cli_doctor_warns_on_missing_billing_and_passes_when_required_ok(ops_checkout, monkeypatch, capsys):
    monkeypatch.delenv("ESMERALDA_BILLING_ACCOUNT", raising=False)
    monkeypatch.setattr(repo, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_tool_version", _fake_version)
    monkeypatch.setattr(repo, "_which_outside_venv", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_check_adc", lambda root: (True, "ADC token available"))
    monkeypatch.setattr(repo, "_check_uv_lock", lambda root: (True, "uv.lock is in sync"))

    assert cli.main(["doctor", "--env", "dev", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["warnings"] == 1
    by_name = {c["name"]: c for c in data["checks"]}
    assert by_name["env:ESMERALDA_BILLING_ACCOUNT"]["status"] == "warn"
    assert by_name["env:ESMERALDA_BILLING_ACCOUNT"]["required"] is False

    assert cli.main(["doctor", "--env", "dev"]) == 0
    assert "All required checks passed" in capsys.readouterr().out


def test_cli_doctor_reports_env_file_source_without_values(ops_checkout, tmp_path, monkeypatch, capsys):
    secret = "synthetic-billing-value"
    path = tmp_path / "esmeralda-env"
    path.write_text(f"ESMERALDA_BILLING_ACCOUNT={secret}\nESMERALDA_ORG_ID=999\n")
    path.chmod(0o600)
    monkeypatch.setenv(envfile.ENV_FILE_VAR, str(path))
    monkeypatch.delenv("ESMERALDA_BILLING_ACCOUNT", raising=False)
    monkeypatch.setenv("ESMERALDA_ORG_ID", "from-shell")
    monkeypatch.setattr(repo, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_tool_version", _fake_version)
    monkeypatch.setattr(repo, "_which_outside_venv", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_check_adc", lambda root: (True, "ADC token available"))
    monkeypatch.setattr(repo, "_check_uv_lock", lambda root: (True, "uv.lock is in sync"))

    assert cli.main(["doctor", "--env", "dev", "--json"]) == 0
    raw = capsys.readouterr().out
    assert secret not in raw and "999" not in raw and "from-shell" not in raw
    data = json.loads(raw)
    by_name = {c["name"]: c for c in data["checks"]}
    assert data["warnings"] == 0
    assert by_name["env_file"]["status"] == "ok"
    assert by_name["env:ESMERALDA_BILLING_ACCOUNT"]["source"] == "env-file"
    assert by_name["env:ESMERALDA_ORG_ID"]["source"] == "env"

    path.write_text(f"ESMERALDA_BILLING_ACCOUNT='{secret}'\nOTHER_KEY=x\n")
    assert cli.main(["doctor", "--env", "dev", "--json"]) == 0
    raw = capsys.readouterr().out
    assert secret not in raw
    check = {c["name"]: c for c in json.loads(raw)["checks"]}["env_file"]
    assert check["status"] == "warn"
    assert "quoted values for ESMERALDA_BILLING_ACCOUNT" in check["detail"] and "OTHER_KEY" in check["detail"]

    # ESMERALDA_ORG_ID unset: still ok (optional), with the folder permission hint.
    monkeypatch.delenv("ESMERALDA_ORG_ID", raising=False)
    assert cli.main(["doctor", "--env", "dev", "--json"]) == 0
    org = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}["env:ESMERALDA_ORG_ID"]
    assert org["status"] == "ok" and org["source"] == "missing"
    assert "resourcemanager.folders.get" in org["detail"]

    path.write_text(f"ESMERALDA_BILLING_ACCOUNT={secret}\n")
    path.chmod(0o640)
    assert cli.main(["doctor", "--env", "dev", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_name = {c["name"]: c for c in data["checks"]}
    assert by_name["env_file"]["status"] == "warn"
    assert "chmod 600" in by_name["env_file"]["detail"]


def test_cli_doctor_exits_nonzero_when_required_check_fails(ops_checkout, monkeypatch, capsys):
    monkeypatch.setattr(repo, "which", lambda name: None if name == "terragrunt" else f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_tool_version", _fake_version)
    monkeypatch.setattr(repo, "_which_outside_venv", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(repo, "_check_adc", lambda root: (False, "missing ADC"))
    monkeypatch.setattr(repo, "_check_uv_lock", lambda root: (True, "ok"))

    assert cli.main(["doctor", "--env", "dev", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["failed"] == 2

    assert cli.main(["doctor", "--env", "dev"]) == 1
    assert "required check(s) failed" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------
# 5. verify & logs
# ------------------------------------------------------------------------------------------


def test_cli_verify_probe_and_skip_query(ops_checkout, monkeypatch, capsys):
    gw_out = (
        "🔎 Probing 2 host(s) through the entry point 10.0.1.3 (*.example.internal) from test-vm-dev...\n"
        "  ✅ legacy-dms.example.internal                        mcp   HTTP 200\n"
        "  ❌ broken.example.internal                            a2a   HTTP 502\n"
    )
    monkeypatch.setattr(
        runtime, "run_gateway_probes", lambda root, env, **_: subprocess.CompletedProcess([], 1, gw_out, "")
    )

    # Filtering to the healthy probe + --skip-query passes:
    assert cli.main(["verify", "--env", "dev", "--probe", "legacy-dms", "--skip-query", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is True and data["gateway"]["ok"] == 1 and data["gateway"]["failed"] == 0

    # Without --probe, the broken route fails verify:
    assert cli.main(["verify", "--env", "dev", "--skip-query"]) == 1
    assert "Verification failed" in capsys.readouterr().out

    # When test_gateway fails before probing any host (0 ok, 0 failed), verify surfaces the fatal error:
    fatal_out = "❌ No test VM outputs in infrastructure/live/dev/layer-5-workloads/test-vm.\n"
    monkeypatch.setattr(
        runtime, "run_gateway_probes", lambda root, env, **_: subprocess.CompletedProcess([], 1, "", fatal_out)
    )
    assert cli.main(["verify", "--env", "dev", "--skip-query", "--json"]) == 1
    fatal_data = json.loads(capsys.readouterr().out)
    assert "No test VM outputs in infrastructure/live/dev/layer-5-workloads/test-vm" in fatal_data["steps"][0]["detail"]


def test_cli_verify_partner(ops_checkout, monkeypatch, capsys):
    pt_out = (
        "🔎 Running live partner conformance probes against https://partner.example.internal (10.0.1.3) from vm...\n"
        "  ✅ Probe 1 (Unauthenticated -> 401): HTTP 401\n"
        "  ✅ Probe 2 (Unallowlisted agent -> 403/404): HTTP 404\n"
        "✅ All live partner front-door conformance probes passed!\n"
    )
    monkeypatch.setattr(
        runtime, "run_partner_probes", lambda root, env, **_: subprocess.CompletedProcess([], 0, pt_out, "")
    )

    assert cli.main(["verify", "--partner", "--env", "dev", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is True and data["partner"]["ok"] == 2 and data["partner"]["mode"] == "live"
    assert len(data["steps"]) == 2

    assert cli.main(["verify", "--partner", "--env", "dev"]) == 0
    assert "Verification passed" in capsys.readouterr().out

    # Apigee not deployed: nothing to probe, one step with the notice.
    notice = "ℹ️  Apigee is disabled in dev (no live state in x): no live probes (make test-apigee checks it offline).\n"
    monkeypatch.setattr(
        runtime, "run_partner_probes", lambda root, env, **_: subprocess.CompletedProcess([], 0, notice, "")
    )
    assert cli.main(["verify", "--partner", "--env", "dev", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["partner"]["mode"] == "offline" and data["partner"]["probes"] == []
    assert [(s["step"], s["passed"], s["detail"]) for s in data["steps"]] == [("partner", True, notice.strip())]


def test_cli_logs_supports_agent_and_mcp_service(ops_checkout, monkeypatch, capsys):
    rows = [{"timestamp": "2026-01-02T03:04:05Z", "severity": "ERROR", "textPayload": "boom"}]
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **_):
        seen_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")

    monkeypatch.setattr(logs, "_client_available", lambda: False)
    monkeypatch.setattr(repo, "run", fake_run)

    assert cli.main(["logs", "legacy-dms", "--env", "dev", "--minutes", "15", "--severity", "ERROR", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["target"] == "legacy-dms" and data["engine"] == "legacy-dms-dev"
    assert data["since"] == "15m" and data["entries"][0]["message"] == "boom"
    assert any('resource.type="cloud_run_revision"' in arg for arg in seen_cmds[-1])

    assert cli.main(["logs", "cx-mortgage-orchestrator", "--env", "dev", "--minutes", "10"]) == 0
    assert "boom" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------
# 6. guard (constructed dynamically so no literal forbidden strings appear in this file)
# ------------------------------------------------------------------------------------------


def _forbidden_samples() -> list[tuple[str, str]]:
    dash = "-" * 5
    return [
        ("customer-nb", "Deployed for " + "nu" + "bank production"),
        ("customer-cl", "Client " + "cla" + "ro configuration"),
        ("customer-nt", "Client " + "natu" + "ra pipeline"),
        ("shortlink-go", "See " + "g" + "o/esmeralda-design for details"),
        ("shortlink-bug", "Fixed in " + "b" + "/123456789"),
        ("shortlink-cl", "Reviewed in " + "c" + "l/987654321"),
        ("corp-host", "Connect to host." + "c" + "orp.goo" + "gle.com"),
        ("gplex-host", "Paste at paste." + "goo" + "gleplex.com"),
        ("g3-marker", "Path under //depot/" + "goo" + "gle3/foo"),
        ("billing-id", "billing_account = " + "015" + "DBB-ABCDEF-123456"),
        ("sandbox-proj", "project = " + "meneg" + "ola-sandbox"),
        ("private-key", f"{dash}BEGIN RSA PRIVATE KEY{dash}"),
        ("api-key", "AI" + "zaSy" + "A" * 33),
        ("gh-token", "gh" + "p_" + "a1B2" * 9),
    ]


def test_guard_detects_all_forbidden_strings_and_masks_snippets():
    for label, line in _forbidden_samples():
        findings = guard.scan_text("sample.py:1", line)
        assert findings, f"Expected guard to flag {label}"

    for safe in (
        "-----BEGIN CERTIFICATE-----",
        "billing_account = " + "012345" + "-6789AB-CDEF01",
        "normal python code x = 1",
    ):
        assert not guard.scan_text("sample.py:1", safe), f"Unexpected finding for {safe}"


def test_guard_path_rules_and_cli_modes(ops_checkout, monkeypatch, capsys):
    dot_gem = "." + "gem" + "ini/config.json"
    for blocked in ("dev.tfvars", "terraform.tfstate", ".env", "server.key", dot_gem, ".local_states/dev/x"):
        assert guard.check_path(blocked), blocked
    assert not guard.check_path(".env.example")
    assert not guard.check_path("packages/esmeralda/src/esmeralda/cli.py")

    bad_line = "+" + "See " + "g" + "o/internal-link\n"
    diff_text = f"diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -0,0 +1,1 @@\n{bad_line}"

    def fake_run(cmd, **_):
        if "--name-only" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "README.md\n", "")
        return subprocess.CompletedProcess(cmd, 0, diff_text, "")

    monkeypatch.setattr(repo, "run", fake_run)
    assert cli.main(["guard", "--staged", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["findings"][0]["where"] == "README.md:1"

    assert cli.main(["guard", "--diff", "origin/main...HEAD"]) == 1
    assert "Sensitive-content check failed" in capsys.readouterr().out
