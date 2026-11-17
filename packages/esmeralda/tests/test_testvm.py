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

"""The test VM resolver (``esmeralda.ops.testvm``) and the gateway probes built on it (offline)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from esmeralda.ops import envfile, repo, runtime, testvm
from esmeralda.ops.repo import OpsError

PEM = "-----BEGIN CERTIFICATE-----\nMIIB'quoted' $HOME `x`\n-----END CERTIFICATE-----"
CA_SECRET = "projects/demo-gw/secrets/internal-ca"

UNITS = [
    "layer-2-networking",
    "layer-3-security",
    "layer-5-workloads/entrypoint",
    "layer-5-workloads/services/legacy-dms",
    "layer-5-workloads/services/corporate-email",
    "layer-5-workloads/test-vm",
]

ROUTES = {
    "corporate-email": {"host": "corporate-email.example.internal", "probe": "mcp", "token": "id", "backend": "direct"},
    "legacy-dms": {"host": "legacy-dms.example.internal", "probe": "mcp", "token": "id", "backend": "direct"},
    "orchestrator": {"host": "orchestrator.example.internal", "probe": "query", "token": "access", "backend": "direct"},
}

STATES = {
    "layer-2-networking": {"entrypoint_address": "10.0.1.3", "entrypoint_domain": "example.internal"},
    "layer-3-security": {"internal_ca_secret_id": CA_SECRET, "probe_unregistered_sa_email": "probe@demo.iam"},
    "layer-5-workloads/entrypoint": {"routes": ROUTES},
    "layer-5-workloads/services/legacy-dms": {"caller_guard": True},
    "layer-5-workloads/services/corporate-email": {"caller_guard": False},
    "layer-5-workloads/test-vm": {"instance_name": "test-vm-dev", "instance_zone": "zone-a", "project_id": "demo-gw"},
}

ATTRIBUTION = {
    "attribution": {
        "policy": {"declared_mismatch": "bill_own_team", "unmapped_caller": "flag"},
        "principal_teams": [
            {"caller": "test-vm", "team": "platform", "cost_centers": ["cc-platform"]},
            {"caller": "orchestrator", "team": "cx", "cost_centers": ["cc-platform", "cc-cx"]},
        ],
    }
}


def _write_state(root: Path, unit: str, outputs: dict) -> None:
    path = root / repo.LIVE / ".local_states" / "dev" / unit / "terraform.tfstate"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 4,
        "outputs": {k: {"value": v, "type": "string"} for k, v in outputs.items()},
        "resources": [{"mode": "managed", "type": "x", "name": "r"}],
    }
    path.write_text(json.dumps(state))


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    live = tmp_path / repo.LIVE
    (live / "dev").mkdir(parents=True)
    (live / "root.hcl").write_text("# root\n")
    (live / "dev" / "env.yaml").write_text('locals {\n  env = "dev"\n}\n')
    for unit in UNITS:
        (live / "dev" / unit).mkdir(parents=True, exist_ok=True)
        (live / "dev" / unit / "terragrunt.hcl").write_text("")
    for unit, outputs in STATES.items():
        _write_state(tmp_path, unit, outputs)
    resolved = tmp_path / "catalog" / ".resolved" / "dev.json"
    resolved.parent.mkdir(parents=True)
    resolved.write_text(json.dumps(ATTRIBUTION))
    probes = tmp_path / runtime.GATEWAY_PROBES
    probes.parent.mkdir(parents=True)
    probes.write_text("echo probes\n")
    monkeypatch.setattr(testvm, "secret", lambda secret_id, **_: PEM if secret_id == CA_SECRET else "")
    monkeypatch.delenv(runtime.QUERY_ENV_VAR, raising=False)
    # No user env file and no partner variables from the developer's shell.
    monkeypatch.setenv(envfile.ENV_FILE_VAR, str(tmp_path / "no-env-file"))
    monkeypatch.delenv(testvm.PARTNER_KEY_VAR, raising=False)
    monkeypatch.delenv(runtime.QUOTA_VAR, raising=False)
    return tmp_path


def test_resolve_reads_the_unit_outputs(checkout):
    vm = testvm.resolve(checkout, "dev")
    assert vm == testvm.TestVM("dev", "test-vm-dev", "zone-a", "demo-gw", "10.0.1.3", "example.internal", PEM)


def test_resolve_falls_back_to_the_legacy_test_vm_state(checkout):
    (checkout / repo.LIVE / ".local_states" / "dev" / "layer-5-workloads" / "test-vm" / "terraform.tfstate").unlink()
    _write_state(checkout, "layer-5-workloads/services/test-vm", STATES["layer-5-workloads/test-vm"])
    assert testvm.resolve(checkout, "dev").name == "test-vm-dev"


@pytest.mark.parametrize(
    ("unit", "outputs", "message"),
    [
        ("layer-5-workloads/test-vm", {}, "No test VM outputs in infrastructure/live/dev/layer-5-workloads/test-vm"),
        ("layer-5-workloads/test-vm", {"instance_name": "vm", "instance_zone": "z"}, "No project_id output"),
        ("layer-2-networking", {}, "No entry point address in layer 2"),
        ("layer-3-security", {}, "No internal_ca_secret_id output in layer 3"),
    ],
)
def test_resolve_errors(checkout, unit, outputs, message):
    _write_state(checkout, unit, outputs)
    with pytest.raises(OpsError, match=message):
        testvm.resolve(checkout, "dev")


def test_secret_calls_gcloud_with_the_short_name(monkeypatch):
    seen = {}

    def fake_run(cmd, **_):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "value\n", "")

    monkeypatch.setattr(repo, "run", fake_run)
    assert testvm.secret("projects/p1/secrets/s1") == "value"
    assert seen["cmd"][-2:] == ["--secret=s1", "--project=p1"]
    with pytest.raises(OpsError, match="Not a Secret Manager secret id"):
        testvm.secret("s1")


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_payload_round_trips_values_through_bash(tmp_path):
    data = tmp_path / "file.py"
    data.write_bytes(b"print('hi')\n\x00binary")
    script = testvm.payload(
        'printf "%s|" "${CA_PEM}" "${ROUTES}" "${EMPTY}"; printf "%s" "${DATA_B64}" | base64 -d > out.bin',
        vars={"CA_PEM": PEM, "ROUTES": "a b c\nd e f", "EMPTY": ""},
        files={"DATA": data},
    )
    result = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, cwd=tmp_path, check=True)
    assert result.stdout == f"{PEM}|a b c\nd e f||"
    assert (tmp_path / "out.bin").read_bytes() == data.read_bytes()


def test_run_sends_the_payload_on_stdin_over_iap(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(repo, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)
    vm = testvm.TestVM("dev", "test-vm-dev", "zone-a", "demo-gw", "10.0.1.3", "example.internal", PEM)
    result = testvm.run(vm, "echo hi", vars={"SECRET": "s3cret"}, timeout=5)
    assert result.stdout == "ok\n"
    assert seen["cmd"] == [
        "gcloud",
        "compute",
        "ssh",
        "test-vm-dev",
        "--zone=zone-a",
        "--project=demo-gw",
        "--tunnel-through-iap",
        "--quiet",
        "--command=bash -s",
    ]
    assert seen["input"] == "SECRET=s3cret\necho hi" and "s3cret" not in " ".join(seen["cmd"])


def test_run_gateway_probes_builds_the_vm_inputs(checkout, monkeypatch):
    seen = {}

    def fake_run(vm, script, *, vars, timeout, **_):
        seen.update(vm=vm, script=script, vars=vars)
        return subprocess.CompletedProcess([], 0, "  ✅ legacy-dms.example.internal mcp id direct HTTP 200\n", "")

    monkeypatch.setattr(testvm, "run", fake_run)
    result = runtime.run_gateway_probes(checkout, "dev")
    assert seen["script"] == "echo probes\n" and seen["vm"].name == "test-vm-dev"
    query = {"user_id": "test-gateway", "message": runtime.DEFAULT_VERIFY_MESSAGE}
    assert seen["vars"] == {
        "ENTRY_IP": "10.0.1.3",
        "ROUTES": "corporate-email.example.internal mcp id direct\n"
        "legacy-dms.example.internal mcp id direct\n"
        "orchestrator.example.internal query access direct",
        "CA_PEM": PEM,
        "QUERY_BODY": json.dumps({"class_method": "async_stream_query", "input": query}, separators=(",", ":")),
        "PROBE_SA": "probe@demo.iam",
        "MISMATCH_MODE": "bill_own_team",
        "UNMAPPED_MODE": "flag",
        "VM_TEAM": "platform",
        "FOREIGN_CC": "cc-cx",
        "GUARDED_HOSTS": " legacy-dms.example.internal",
    }
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == (
        "🔎 Probing 3 host(s) through the entry point 10.0.1.3 (*.example.internal) from test-vm-dev..."
    )
    assert result.stdout.splitlines()[-1] == "✅ Every route answered through the entry point."
    parsed = runtime.parse_gateway_output(result.stdout)
    assert parsed["entry_point"] == {"ip": "10.0.1.3", "domain": "example.internal"}
    assert parsed["hosts"] == [{"host": "legacy-dms.example.internal", "probe": "mcp", "ok": True, "http": 200}]


def test_run_gateway_probes_defaults_without_attribution(checkout, monkeypatch):
    (checkout / "catalog" / ".resolved" / "dev.json").unlink()
    security = {"internal_ca_secret_id": CA_SECRET, "probe_unregistered_sa_email": None}
    _write_state(checkout, "layer-3-security", security)
    monkeypatch.setenv(runtime.QUERY_ENV_VAR, "Who is Julian?")
    seen = {}
    monkeypatch.setattr(
        testvm, "run", lambda vm, script, *, vars, **_: seen.update(vars) or subprocess.CompletedProcess([], 1, "", "")
    )
    result = runtime.run_gateway_probes(checkout, "dev")
    assert result.returncode == 1
    assert result.stderr.endswith("❌ Some routes failed through the entry point (see above).\n")
    assert seen["PROBE_SA"] == "" and seen["MISMATCH_MODE"] == "reject" and seen["UNMAPPED_MODE"] == "deny"
    assert seen["VM_TEAM"] == "" and seen["FOREIGN_CC"] == ""
    assert json.loads(seen["QUERY_BODY"])["input"]["message"] == "Who is Julian?"


def test_run_gateway_probes_reports_resolution_errors(checkout, monkeypatch):
    _write_state(checkout, "layer-5-workloads/entrypoint", {})
    monkeypatch.setattr(testvm, "run", lambda *a, **k: pytest.fail("must not reach the VM"))
    result = runtime.run_gateway_probes(checkout, "dev")
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr.startswith("❌ No routes output in infrastructure/live/dev/layer-5-workloads/entrypoint.")
    summary = runtime.test_gateway(checkout, "dev")
    assert summary["passed"] is False and summary["hosts"] == []
    assert summary["error"].startswith("❌ No routes output")


def test_run_gateway_probes_needs_the_vm_payload(checkout):
    (checkout / runtime.GATEWAY_PROBES).unlink()
    with pytest.raises(OpsError, match="scripts/vm/gateway_probes.sh not found"):
        runtime.run_gateway_probes(checkout, "dev")


def test_gateway_probes_payload_is_valid_bash():
    script = Path(__file__).resolve().parents[3] / runtime.GATEWAY_PROBES
    if not shutil.which("bash") or not script.is_file():
        pytest.skip("needs bash and the repository's scripts/vm/gateway_probes.sh")
    subprocess.run(["bash", "-n", str(script)], check=True)


# ---- partner front door (esmeralda verify --partner) ------------------------------------------

KEY_SECRET = "projects/demo-apigee/secrets/partner-smoke-test-key"


def _deploy_apigee(root: Path, *, apigee_env: str | None = "eval", key_secret: str | None = KEY_SECRET) -> None:
    for unit in (testvm.APIGEE_PLATFORM_UNIT, testvm.APIGEE_PRODUCTS_UNIT):
        unit_dir = root / repo.LIVE / "dev" / unit
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / "terragrunt.hcl").write_text("")
    _write_state(root, testvm.APIGEE_PLATFORM_UNIT, {"apigee_env_name": apigee_env} if apigee_env else {})
    _write_state(root, testvm.APIGEE_PRODUCTS_UNIT, {"partner_consumer_key_secret_id": key_secret})
    for path in runtime.PARTNER_FILES.values():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(f"# {path.name}\n")


@pytest.fixture
def secrets(monkeypatch):
    values = {CA_SECRET: PEM, KEY_SECRET: "partner-key"}
    monkeypatch.setattr(testvm, "secret", lambda secret_id, **_: values.get(secret_id, ""))
    return values


def test_apigee_env(checkout):
    assert testvm.apigee_env(checkout, "dev") == ""  # no apigee/platform unit
    _deploy_apigee(checkout, apigee_env=None)
    assert testvm.apigee_env(checkout, "dev") == ""  # unit present, nothing deployed
    _deploy_apigee(checkout)
    assert testvm.apigee_env(checkout, "dev") == "eval"


def test_partner_key_prefers_the_environment_then_the_secret(checkout, secrets, monkeypatch):
    _deploy_apigee(checkout)
    assert testvm.partner_key(checkout, "dev") == "partner-key"
    monkeypatch.setenv(testvm.PARTNER_KEY_VAR, "from-env")
    assert testvm.partner_key(checkout, "dev") == "from-env"
    monkeypatch.delenv(testvm.PARTNER_KEY_VAR)
    env_file = checkout / "env"
    env_file.write_text(f"{testvm.PARTNER_KEY_VAR}=from-env-file\n")
    monkeypatch.setenv(envfile.ENV_FILE_VAR, str(env_file))
    assert testvm.partner_key(checkout, "dev") == "from-env-file"


def test_partner_key_missing(checkout, secrets):
    _deploy_apigee(checkout, key_secret=None)
    with pytest.raises(OpsError, match="Set ESMERALDA_PARTNER_API_KEY or deploy layer-5-workloads/apigee/products"):
        testvm.partner_key(checkout, "dev")


def test_partner_probes_without_apigee_are_a_notice(checkout, monkeypatch):
    monkeypatch.setattr(testvm, "run", lambda *a, **k: pytest.fail("must not reach the VM"))
    result = runtime.run_partner_probes(checkout, "dev")
    assert result.returncode == 0 and result.stdout.startswith("ℹ️  Apigee platform directory not deployed in dev")
    _deploy_apigee(checkout, apigee_env=None)
    summary = runtime.test_partner(checkout, "dev")
    assert summary["passed"] is True and summary["mode"] == "offline" and summary["probes"] == []
    assert summary["notice"].startswith("ℹ️  Apigee is disabled in dev")
    assert "make test-apigee" in summary["notice"]


def test_partner_probes_ship_the_catalog_to_the_vm(checkout, secrets, monkeypatch):
    _deploy_apigee(checkout)
    monkeypatch.setenv(runtime.QUOTA_VAR, "1")
    seen = {}

    def fake_run(vm, script, *, vars, files, timeout, **_):
        seen.update(vm=vm, script=script, vars=vars, files=files)
        return subprocess.CompletedProcess([], 0, "  ✅ Probe 1 (Unauthenticated -> 401): HTTP 401\n", "")

    monkeypatch.setattr(testvm, "run", fake_run)
    summary = runtime.test_partner(checkout, "dev")
    assert seen["script"] == runtime.PARTNER_REMOTE
    assert seen["vars"] == {
        "PARTNER_ENTRY_IP": "10.0.1.3",
        "PARTNER_HOST": "partner.example.internal",
        "PARTNER_API_KEY": "partner-key",
        "CA_PEM": PEM,
        "ESMERALDA_TEST_QUOTA_EXHAUSTION": "1",
    }
    assert seen["files"] == {key: checkout / path for key, path in runtime.PARTNER_FILES.items()}
    assert summary["passed"] is True and summary["mode"] == "live" and summary["ok"] == 1
    assert summary["probes"] == [
        {"probe": 1, "ok": True, "detail": "HTTP 401", "label": "Unauthenticated -> 401"},
    ]


def test_partner_probe_failures_and_resolution_errors(checkout, secrets, monkeypatch):
    _deploy_apigee(checkout)
    monkeypatch.setattr(
        testvm, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, "  ❌ Probe 4 (SSE): HTTP 504\n", "")
    )
    summary = runtime.test_partner(checkout, "dev")
    assert summary["passed"] is False and summary["failed"] == 1 and summary["error"] == "❌ Probe 4 (SSE): HTTP 504"

    _write_state(checkout, "layer-5-workloads/test-vm", {})
    summary = runtime.test_partner(checkout, "dev")
    assert summary["passed"] is False and summary["error"].startswith("❌ No test VM outputs")


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_partner_remote_runs_probe_py_with_the_shipped_files(tmp_path):
    """The VM half, run locally with a stub probe.py that prints what it got."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, pathlib\n"
        "env = os.environ\n"
        "print(env['PARTNER_HOST'], env['PARTNER_API_KEY'], env['ESMERALDA_TEST_QUOTA_EXHAUSTION'])\n"
        "print(pathlib.Path(os.environ['PARTNER_CA_FILE']).read_text().strip() == os.environ.get('EXPECT', ''))\n"
        "print((pathlib.Path(__file__).parent / 'scenarios.py').read_text().strip())\n"
    )
    scenarios = tmp_path / "scenarios.py"
    scenarios.write_text("SCENARIOS = []\n")
    script = testvm.payload(
        runtime.PARTNER_REMOTE,
        vars={
            "PARTNER_ENTRY_IP": "10.0.1.3",
            "PARTNER_HOST": "partner.example.internal",
            "PARTNER_API_KEY": "k'ey",
            "CA_PEM": "pem",
            "ESMERALDA_TEST_QUOTA_EXHAUSTION": "0",
        },
        files={"SCENARIOS": scenarios, "PROBE": probe},
    )
    result = subprocess.run(
        ["bash", "-s"], input="export EXPECT=pem\n" + script, capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines() == ["partner.example.internal k'ey 0", "True", "SCENARIOS = []"]
