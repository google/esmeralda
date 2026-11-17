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

"""esmeralda.ops.lock: the offline ``uv.lock`` check and the Cloud Build lock (subprocesses faked)."""

from __future__ import annotations

import json
import re
import subprocess
import types
from pathlib import Path

import pytest
import yaml

from esmeralda import cli
from esmeralda.ops import build, lock, repo
from esmeralda.ops.repo import OpsError

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS = build.BuildSettings("esm-cicd-test", "europe-docker.pkg.dev/x/dev", "builder@x.iam", "esm-build-src")


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["apps/a", "packages/b"]\n')
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "README.md").write_text("readme\n")
    for member in ("apps/a", "packages/b"):
        (tmp_path / member).mkdir(parents=True)
        (tmp_path / member / "pyproject.toml").write_text(f'[project]\nname = "{member.split("/")[1]}"\n')
    (tmp_path / "apps/a/main.py").write_text("not uploaded\n")
    (tmp_path / repo.LIVE).mkdir(parents=True)
    (tmp_path / repo.LIVE / "root.hcl").write_text("")
    return tmp_path


@pytest.fixture
def uv_check(monkeypatch):
    """Fakes ``uv lock --check``: records the call, returns ``state['code']``."""
    state = {"code": 0, "calls": [], "stderr": ""}

    def run(cmd, **kwargs):
        state["calls"].append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, state["code"], "", state["stderr"])

    monkeypatch.setattr(lock, "subprocess", types.SimpleNamespace(
        run=run, DEVNULL=subprocess.DEVNULL, SubprocessError=subprocess.SubprocessError,
        TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess,
    ))  # fmt: skip
    monkeypatch.setattr(repo, "which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setenv("UV_FROZEN", "1")
    return state


def test_check_runs_the_offline_public_index_check_without_uv_frozen(workspace, uv_check):
    assert lock.check(workspace) == {"status": "ok", "detail": "uv.lock is in sync with pyproject.toml"}
    cmd, kwargs = uv_check["calls"][0]
    assert cmd == ["uv", "lock", "--check", "--offline", "--default-index", "https://pypi.org/simple"]
    assert kwargs["cwd"] == workspace and "UV_FROZEN" not in kwargs["env"]


def test_check_reports_a_stale_lock(workspace, uv_check):
    uv_check["code"], uv_check["stderr"] = 1, "error: The lockfile needs to be updated\n"
    result = lock.check(workspace)
    assert result["status"] == "stale"
    assert "needs to be updated" in result["detail"] and "make lock" in result["detail"]


def test_check_skips_without_uv_and_fails_without_a_lock(workspace, uv_check, monkeypatch):
    monkeypatch.setattr(repo, "which", lambda binary: None)
    assert lock.check(workspace)["status"] == "skipped" and not uv_check["calls"]
    (workspace / "uv.lock").unlink()
    assert lock.check(workspace)["status"] == "error"


def test_doctor_uses_the_same_check(workspace, monkeypatch):
    monkeypatch.setattr(lock, "check", lambda root: {"status": "stale", "detail": "stale!"})
    assert repo._check_uv_lock(workspace) == (False, "stale!")
    monkeypatch.setattr(lock, "check", lambda root: {"status": "skipped", "detail": "no uv"})
    assert repo._check_uv_lock(workspace) == (True, "no uv")


def test_validate_packages():
    assert lock.validate_packages(["urllib3", "google-adk==2.11.0", "a.b_c==1.0rc1+local"]) == [
        "urllib3", "google-adk==2.11.0", "a.b_c==1.0rc1+local",
    ]  # fmt: skip
    for bad in (["urllib3; rm -rf /"], ["--index-url=x"], ["a>=1"], [""]):
        with pytest.raises(OpsError, match="Invalid package spec"):
            lock.validate_packages(bad)


def test_input_files_are_the_workspace_manifests_and_the_lock(workspace):
    assert lock.input_files(workspace) == [
        "pyproject.toml", "uv.lock", "README.md", "apps/a/pyproject.toml", "packages/b/pyproject.toml",
    ]  # fmt: skip


def test_cloudbuild_yaml():
    config = yaml.safe_load(lock.cloudbuild_yaml(["urllib3", "a==1"], bucket="bkt", run_id="uvlock-1"))
    step = config["steps"][0]
    assert step["name"] == lock.UV_IMAGE and step["entrypoint"] == "uv"
    assert step["args"] == ["lock", "--upgrade-package", "urllib3", "--upgrade-package", "a==1"]
    assert config["artifacts"]["objects"] == {"location": "gs://bkt/uvlock-1/", "paths": ["uv.lock"]}
    assert config["logsBucket"] == "gs://bkt/uvlock-1/logs" and config["options"]["logging"] == "GCS_ONLY"


def test_uv_version_matches_the_service_images():
    for dockerfile in sorted((REPO_ROOT / "apps" / "services").glob("*/Dockerfile")):
        for version in re.findall(r"ghcr\.io/astral-sh/uv:([0-9.]+)", dockerfile.read_text()):
            assert version == lock.UV_VERSION, dockerfile


@pytest.fixture
def cloud(workspace, monkeypatch):
    """Fakes layer 0, gcloud builds submit and gcloud storage cp; ``state['new_lock']`` comes back."""
    state = {"submit": [], "fetch": [], "submit_code": 0, "new_lock": "version = 1\n# relocked\n", "uploaded": {}}
    monkeypatch.setattr(build, "resolve_cicd_dir", lambda root, override=None: (root / build.CICD_UNIT, True))
    monkeypatch.setattr(build, "build_settings", lambda cicd_dir: SETTINGS)
    monkeypatch.setattr(repo, "which", lambda binary: f"/usr/bin/{binary}")

    def submit(cmd, **kwargs):
        work = Path(cmd[3])
        state["uploaded"] = sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file())
        state["config"] = yaml.safe_load((work / "cloudbuild.yaml").read_text())
        state["submit"].append(cmd)
        return subprocess.CompletedProcess(cmd, state["submit_code"])

    def fetch(cmd, *, cwd, timeout, what):
        state["fetch"].append(cmd)
        Path(cmd[-1]).write_text(state["new_lock"])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(lock, "subprocess", types.SimpleNamespace(
        run=submit, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired,
    ))  # fmt: skip
    monkeypatch.setattr(lock.repo, "run", fetch)
    monkeypatch.setattr(lock, "check", lambda root: {"status": "ok", "detail": "in sync"})
    monkeypatch.setattr(lock, "_lock_changed", lambda root: True)
    return state


def test_lock_submits_the_manifests_and_writes_the_new_lock_back(workspace, cloud):
    data = lock.lock(workspace, ["urllib3"])
    assert data["exit_code"] == 0 and data["changed"] is True and data["cicd_from_main_checkout"] is True
    assert (workspace / "uv.lock").read_text() == cloud["new_lock"]
    assert cloud["uploaded"] == [
        "README.md", "apps/a/pyproject.toml", "cloudbuild.yaml", "packages/b/pyproject.toml", "pyproject.toml",
        "uv.lock",
    ]  # fmt: skip
    assert cloud["config"]["steps"][0]["args"] == ["lock", "--upgrade-package", "urllib3"]
    submit = cloud["submit"][0]
    assert submit[:3] == ["gcloud", "builds", "submit"]
    assert "--project=esm-cicd-test" in submit
    assert "--service-account=projects/esm-cicd-test/serviceAccounts/builder@x.iam" in submit
    assert "--gcs-source-staging-dir=gs://esm-build-src/source" in submit
    run_id = cloud["config"]["artifacts"]["objects"]["location"].split("/")[3]
    assert cloud["fetch"][0][-2] == f"gs://esm-build-src/{run_id}/uv.lock"


def test_lock_leaves_uv_lock_alone_when_cloud_build_fails(workspace, cloud):
    cloud["submit_code"] = 1
    data = lock.lock(workspace)
    assert data["exit_code"] == 1 and "Cloud Build failed" in data["error"]
    assert (workspace / "uv.lock").read_text() == "version = 1\n" and not cloud["fetch"]


def test_lock_dry_run_runs_nothing(workspace, cloud):
    data = lock.lock(workspace, dry_run=True)
    assert data["dry_run"] is True and data["exit_code"] == 0 and not cloud["submit"]
    assert data["commands"][0].startswith("gcloud builds submit '$WORK'")


def test_lock_dry_run_without_layer_0_shows_placeholders(workspace, cloud, monkeypatch):
    def no_outputs(cicd_dir):
        raise OpsError("No layer-0 CI/CD outputs")

    monkeypatch.setattr(build, "build_settings", no_outputs)
    data = lock.lock(workspace, dry_run=True)
    assert "--project=$CICD_PROJ" in data["commands"][0] and "No layer-0" in data["note"]


def test_cli_lock_check_exit_codes(workspace, monkeypatch, capsys):
    monkeypatch.setattr(lock, "check", lambda root: {"status": "ok", "detail": "uv.lock is in sync"})
    assert cli.main(["lock", "--check", "--repo", str(workspace)]) == 0
    assert "in sync" in capsys.readouterr().out
    monkeypatch.setattr(lock, "check", lambda root: {"status": "stale", "detail": "out of date"})
    assert cli.main(["lock", "--check", "--repo", str(workspace), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "stale"
    assert cli.main(["lock", "--check", "urllib3", "--repo", str(workspace)]) == 2


def test_cli_lock(workspace, cloud, capsys):
    assert cli.main(["lock", "urllib3==2.0", "--repo", str(workspace)]) == 0
    assert "uv.lock updated" in capsys.readouterr().out
    assert cli.main(["lock", "bad;pkg", "--repo", str(workspace)]) == 2
