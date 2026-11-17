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

"""The partner load test workstation half (``esmeralda.ops.partner_load``), offline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from esmeralda.ops import partner_load, repo, testvm
from esmeralda.ops.repo import OpsError

VM = testvm.TestVM("dev", "test-vm-dev", "zone-a", "demo-gw", "10.0.1.3", "example.internal", "pem")
STATS = (
    "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,"
    "Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%\n"
    "POST,orchestrator-income,4,0,9000,9100,8000,10000,512,0.1,0,9000,9200,9300,9400,9500,9900,10000,10000\n"
)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "locust"\nversion = "9.8.7"\nsource = {}\n')
    for path in [partner_load.VM_SCRIPT, *partner_load.FILES.values()]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(f"# {path.name}\n")
    monkeypatch.setattr(testvm, "apigee_env", lambda root, env: "eval")
    monkeypatch.setattr(testvm, "resolve", lambda root, env: VM)
    monkeypatch.setattr(testvm, "partner_key", lambda root, env: "partner-key")
    return tmp_path


def test_parse_args_defaults_and_validation(capsys):
    args = partner_load.parse_args([])
    assert (args.env, args.users, args.spawn_rate, args.duration, args.stop_timeout, args.include_quota) == (
        "dev",
        2,
        1,
        "5m",
        90,
        False,
    )
    args = partner_load.parse_args(["prd", "--users", "1", "--duration", "90s", "--include-quota"])
    assert (args.env, args.users, args.duration, args.include_quota) == ("prd", 1, "90s", True)
    for bad in (["--duration", "5 minutes"], ["--users", "-1"], ["--users", "x"]):
        with pytest.raises(SystemExit):
            partner_load.parse_args(bad)
    capsys.readouterr()


def test_helpers(checkout, tmp_path_factory):
    assert partner_load.seconds("90s") == 90 and partner_load.seconds("5m") == 300
    assert partner_load.seconds("1h") == 3600 and partner_load.seconds("45") == 45
    assert partner_load.spawn_rate(1.0) == "1" and partner_load.spawn_rate(0.5) == "0.5"
    assert partner_load.locust_version(checkout) == "9.8.7"
    assert partner_load.locust_version(tmp_path_factory.mktemp("empty")) == partner_load.DEFAULT_LOCUST_VERSION


def test_summary_lines(tmp_path):
    (tmp_path / "partner_stats.csv").write_text(STATS)
    (tmp_path / "partner_failures.csv").write_text("Method,Name,Error,Occurrences\nPOST,x,HTTP 504,1\n")
    lines = partner_load.summary_lines(tmp_path)
    assert lines[0] == "📊 Per-scenario results (ms) — tests/load_test/.results/partner/partner_stats.csv"
    assert (
        lines[1]
        == f"  {'orchestrator-income':<48} {'POST':<6} {'4':>6} req {'0':>5} fail  p50 {'9000':>7}  p95 {'9900':>7}"
    )
    assert lines[2:] == ["❌ Failures — tests/load_test/.results/partner/partner_failures.csv", "  POST,x,HTTP 504,1"]


def test_run_ships_the_payload_and_copies_the_reports(checkout, monkeypatch, capsys):
    seen: dict = {}

    def fake_run(vm, script, *, vars, files, timeout, echo):
        seen.update(vm=vm, script=script, vars=vars, files=files, timeout=timeout, echo=echo)
        return subprocess.CompletedProcess([], 0, "…\nLOCUST_EXIT=0\n", "")

    def fake_scp(vm, remote, local, **_):
        seen["scp"] = remote
        (local / "partner_stats.csv").write_text(STATS)
        return True

    monkeypatch.setattr(testvm, "run", fake_run)
    monkeypatch.setattr(partner_load, "scp_from", fake_scp)
    monkeypatch.setattr(partner_load, "cleanup", lambda vm, remote_dir: seen.update(cleanup=remote_dir))
    results = checkout / partner_load.RESULTS_DIR
    results.mkdir(parents=True)
    (results / "partner_stale.csv").write_text("old")

    args = partner_load.parse_args(["dev", "--users", "1", "--duration", "1m"])
    assert partner_load.run(checkout, args) == 0

    run_id = seen["vars"]["PARTNER_LOAD_RUN_ID"]
    assert seen["vars"] == {
        "PARTNER_ENTRY_IP": "10.0.1.3",
        "PARTNER_HOST": "partner.example.internal",
        "PARTNER_API_KEY": "partner-key",
        "CA_PEM": "pem",
        "PARTNER_INCLUDE_QUOTA": "0",
        "PARTNER_LOAD_RUN_ID": run_id,
        "REMOTE_DIR": f"esmeralda-partner-load/{run_id}",
        "LOCUST_VERSION": "9.8.7",
        "UV_VERSION": partner_load.UV_VERSION,
        "USERS": "1",
        "SPAWN_RATE": "1",
        "DURATION": "1m",
        "STOP_TIMEOUT": "90",
    }
    assert seen["files"] == {k: checkout / p for k, p in partner_load.FILES.items()}
    assert seen["script"] == "# partner_load.sh\n" and seen["echo"] is True and seen["timeout"] == 60 + 90 + 900
    assert seen["scp"] == f"esmeralda-partner-load/{run_id}/results/*"
    assert seen["cleanup"] == f"esmeralda-partner-load/{run_id}"
    assert not (results / "partner_stale.csv").exists()
    out = capsys.readouterr().out
    assert f"⚡ Partner load test {run_id}: 1 user(s), 1m, quota exhaustion off" in out
    assert "orchestrator-income" in out and f"x-esmeralda-load-run={run_id}" in out


def test_run_returns_the_locust_exit_code(checkout, monkeypatch):
    monkeypatch.setattr(testvm, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "LOCUST_EXIT=1\n", ""))
    monkeypatch.setattr(
        partner_load, "scp_from", lambda vm, remote, local, **_: (local / "partner_stats.csv").write_text(STATS) > 0
    )
    monkeypatch.setattr(partner_load, "cleanup", lambda *a: None)
    assert partner_load.run(checkout, partner_load.parse_args([])) == 1


def test_run_errors(checkout, monkeypatch):
    monkeypatch.setattr(testvm, "run", lambda *a, **k: subprocess.CompletedProcess([], 255, "ssh: timeout\n", ""))
    with pytest.raises(OpsError, match=r"The remote run did not finish \(ssh exit 255\)"):
        partner_load.run(checkout, partner_load.parse_args([]))

    monkeypatch.setattr(testvm, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "LOCUST_EXIT=0\n", ""))
    monkeypatch.setattr(partner_load, "scp_from", lambda *a, **k: False)
    with pytest.raises(OpsError, match="Could not copy the reports back"):
        partner_load.run(checkout, partner_load.parse_args([]))

    monkeypatch.setattr(testvm, "apigee_env", lambda root, env: "")
    with pytest.raises(OpsError, match="Apigee is not deployed in dev: nothing to load test."):
        partner_load.run(checkout, partner_load.parse_args([]))


def test_main_prints_errors(checkout, monkeypatch, capsys):
    monkeypatch.setattr(repo, "repo_root", lambda: checkout)
    monkeypatch.setattr(testvm, "apigee_env", lambda root, env: "")
    assert partner_load.main(["dev"]) == 1
    assert capsys.readouterr().err == "❌ Apigee is not deployed in dev: nothing to load test.\n"


def test_vm_payload_is_valid_bash():
    script = Path(__file__).resolve().parents[3] / partner_load.VM_SCRIPT
    if not shutil.which("bash") or not script.is_file():
        pytest.skip("needs bash and the repository's scripts/vm/partner_load.sh")
    subprocess.run(["bash", "-n", str(script)], check=True)
