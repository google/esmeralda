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

"""``esmeralda deploy`` / ``destroy`` / ``build`` / ``release`` (Terragrunt and gcloud mocked)."""

from __future__ import annotations

import datetime
import json
import subprocess
import urllib.error
import zipfile
from pathlib import Path

import pytest

from esmeralda import cli
from esmeralda.ops import build, deploy, envfile, infra, release, repo
from esmeralda.ops import release_manifest as rm
from esmeralda.ops.repo import OpsError

ENV_YAML = """
locals {
  environment      = "dev"
  region           = "us-central1"
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

W = "layer-5-workloads"
UNITS = {
    "layer-1-projects": [],
    "layer-2-networking": ["../layer-1-projects"],
    "layer-3-security": ["../../shared/layer-0-cicd", "../layer-2-networking"],
    "layer-4-governance": ["../layer-3-security"],
    f"{W}/agents/ai-coe-mortgage-specialist": ["../../../layer-4-governance"],
    f"{W}/agents/cx-mortgage-orchestrator": ["../../../layer-4-governance"],
    f"{W}/services/legacy-dms": ["../../agents/cx-mortgage-orchestrator"],
    f"{W}/gateways/direct": ["../../services/legacy-dms", "../../agents/ai-coe-mortgage-specialist"],
    f"{W}/gateways/envoy": ["../../services/legacy-dms"],
    f"{W}/entrypoint": ["../gateways/direct", "../gateways/envoy"],
    f"{W}/iap-egress": ["../services/legacy-dms"],
    f"{W}/test-vm": ["../../layer-3-security"],
    f"{W}/partner-mcp": [],  # ordering-only dependency on the entry point (below)
}
EXTRA_HCL = {
    f"{W}/gateways/envoy": 'exclude {\n  if = !contains(local.env.gateways.enabled, "envoy")\n  actions = ["all"]\n}\n',
    f"{W}/partner-mcp": 'dependencies {\n  paths = ["../entrypoint"]\n}\n',
}
LAYER5_ORDER = [
    f"{W}/agents/ai-coe-mortgage-specialist",
    f"{W}/agents/cx-mortgage-orchestrator",
    f"{W}/test-vm",
    f"{W}/services/legacy-dms",
    f"{W}/gateways/direct",
    f"{W}/entrypoint",
    f"{W}/partner-mcp",
    f"{W}/iap-egress",
]
CICD_OUTPUTS = {
    "cicd_project_id": "demo-cicd",
    "dev_repository_url": "us-central1-docker.pkg.dev/demo-cicd/containers",
    "release_repository_url": "us-central1-docker.pkg.dev/demo-cicd/release",
    "builder_sa_email": "builder@demo-cicd.iam.gserviceaccount.com",
    "build_source_bucket": "demo-cicd-build-source",
}


@pytest.fixture(autouse=True)
def _no_user_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv(envfile.ENV_FILE_VAR, str(tmp_path / "no-such-env-file"))
    monkeypatch.delenv(build.CICD_DIR_ENV, raising=False)
    monkeypatch.delenv("BUILD_TAG", raising=False)


def _state(resources: int = 1, outputs: dict | None = None) -> str:
    return json.dumps(
        {
            "version": 4,
            "outputs": {k: {"value": v, "type": "string"} for k, v in (outputs or {}).items()},
            "resources": [{"mode": "managed", "type": "t", "name": f"r{i}", "instances": []} for i in range(resources)],
        }
    )


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    live = tmp_path / "infrastructure" / "live"
    (live / "dev").mkdir(parents=True)
    (live / "prd").mkdir(parents=True)
    (live / "root.hcl").write_text("# root\n")
    (live / "dev" / "env.yaml").write_text(ENV_YAML)
    (live / "prd" / "env.yaml").write_text(ENV_YAML.replace('"dev"', '"prd"'))
    (live / "shared" / "layer-0-cicd").mkdir(parents=True)
    (live / "shared" / "layer-0-cicd" / "terragrunt.hcl").write_text("# layer 0\n")
    for unit, deps in UNITS.items():
        udir = live / "dev" / unit
        udir.mkdir(parents=True, exist_ok=True)
        text = "".join(f'dependency "d{i}" {{\n  config_path = "{d}"\n}}\n' for i, d in enumerate(deps))
        (udir / "terragrunt.hcl").write_text(text + EXTRA_HCL.get(unit, ""))
    for unit in UNITS:
        state = live / ".local_states" / "dev" / unit / "terraform.tfstate"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(_state())
    cicd_state = tmp_path / build.CICD_STATE
    cicd_state.parent.mkdir(parents=True, exist_ok=True)
    cicd_state.write_text(_state(3, CICD_OUTPUTS))
    monkeypatch.setenv(repo.REPO_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(build, "git_sha", lambda root: "abc1234")
    return tmp_path


def _quiet() -> deploy.Options:
    return deploy.Options(progress=lambda msg: None)


# ------------------------------------------------------------------------------------------
# order
# ------------------------------------------------------------------------------------------


def test_dependencies_block_counts_as_a_dependency(checkout):
    assert f"{W}/entrypoint" in infra.unit_dependencies(checkout, "dev", f"{W}/partner-mcp")


def test_workload_order_follows_terragrunt_dag_skips_excluded_and_ends_with_iap_egress(checkout):
    order = deploy.workload_order(checkout, "dev")
    assert order == LAYER5_ORDER
    assert f"{W}/gateways/envoy" not in order  # excluded in env.yaml
    assert order[-1] == deploy.FINAL_UNIT


def test_all_steps_match_make_deploy_all(checkout):
    steps = deploy.plan_steps(checkout, "dev", all_layers=True)
    assert [(s.kind, s.name) for s in steps] == [
        ("unit", "shared/layer-0-cicd"),
        ("unit", "layer-1-projects"),
        ("unit", "layer-2-networking"),
        ("unit", "layer-3-security"),
        ("unit", "layer-4-governance"),
        ("build", "images"),
        *(("unit", u) for u in LAYER5_ORDER),
        ("verify-images", "dev"),
    ]
    skipped = deploy.plan_steps(checkout, "dev", all_layers=True, skip_build=True, skip_verify=True)
    assert all(s.kind == "unit" for s in skipped)


def test_layer_and_unit_selection(checkout):
    assert [s.name for s in deploy.plan_steps(checkout, "dev", layers=[3, 1, 2])] == [
        "layer-1-projects",
        "layer-2-networking",
        "layer-3-security",
    ]
    assert [s.name for s in deploy.plan_steps(checkout, "dev", layers=[0])] == ["shared/layer-0-cicd"]
    # named units are resolved (short names) and sorted into deploy order
    steps = deploy.plan_steps(checkout, "dev", units=["entrypoint", "agents/cx-mortgage-orchestrator", "entrypoint"])
    assert [s.name for s in steps] == [f"{W}/agents/cx-mortgage-orchestrator", f"{W}/entrypoint"]
    with pytest.raises(OpsError, match="excluded"):
        deploy.plan_steps(checkout, "dev", units=["gateways/envoy"])
    with pytest.raises(OpsError, match="Choose what to deploy"):
        deploy.plan_steps(checkout, "dev")
    with pytest.raises(OpsError, match="Choose what to deploy"):
        deploy.plan_steps(checkout, "dev", layers=[1], all_layers=True)


# ------------------------------------------------------------------------------------------
# deploy run: order, destroy guard, stop on failure, dry run
# ------------------------------------------------------------------------------------------


def _record_apply(monkeypatch, *, fail: str | None = None, refuse: str | None = None) -> list[tuple]:
    calls: list[tuple] = []

    def fake_apply(root, env, unit, *, allow_destroy=False, timeout=900.0, directory=None):
        calls.append((unit, allow_destroy, timeout, directory))
        if unit == fail:
            raise OpsError(f"terragrunt apply ({unit}) failed (exit 1): boom")
        if unit == refuse:
            return {
                "env": env,
                "unit": unit,
                "counts": {"destroy": 1},
                "applied": False,
                "refused": True,
                "exit_code": 2,
                "reason": "Refusing to apply",
            }
        return {"env": env, "unit": unit, "counts": {"add": 1}, "applied": True, "refused": False, "exit_code": 0}

    monkeypatch.setattr(infra, "apply_unit", fake_apply)
    return calls


def test_deploy_applies_in_order_and_passes_allow_destroy_and_timeout(checkout, monkeypatch):
    calls = _record_apply(monkeypatch)
    steps = deploy.plan_steps(checkout, "dev", layers=[1, 2, 3, 4])
    opts = _quiet()
    opts.allow_destroy, opts.timeout = True, 42.0
    data = deploy.deploy(checkout, "dev", steps, opts)
    assert data["ok"] and data["exit_code"] == 0
    assert [c[0] for c in calls] == ["layer-1-projects", "layer-2-networking", "layer-3-security", "layer-4-governance"]
    assert all(c[1] is True and c[2] == 42.0 for c in calls)
    assert [s["status"] for s in data["steps"]] == ["applied"] * 4


def test_deploy_layer0_runs_in_the_cicd_directory(checkout, monkeypatch):
    calls = _record_apply(monkeypatch)
    data = deploy.deploy(checkout, "dev", deploy.plan_steps(checkout, "dev", layers=[0]), _quiet())
    assert data["ok"]
    assert calls[0][0] == infra.CICD_UNIT and calls[0][3] == checkout / build.CICD_UNIT


def test_deploy_layer0_refuses_from_a_worktree_whose_state_is_in_the_main_checkout(checkout, monkeypatch):
    calls = _record_apply(monkeypatch)
    main = checkout / "main-checkout"
    monkeypatch.setattr(build, "resolve_cicd_dir", lambda root, override=None: (main / build.CICD_UNIT, True))
    data = deploy.deploy(checkout, "dev", deploy.plan_steps(checkout, "dev", layers=[0, 1]), _quiet())
    assert not data["ok"] and data["exit_code"] == 1 and calls == []
    assert "main checkout" in data["steps"][0]["error"]
    assert data["steps"][1]["status"] == "pending"


def test_deploy_stops_at_the_first_failure(checkout, monkeypatch):
    calls = _record_apply(monkeypatch, fail="layer-2-networking")
    data = deploy.deploy(checkout, "dev", deploy.plan_steps(checkout, "dev", layers=[1, 2, 3, 4]), _quiet())
    assert data["exit_code"] == 1 and not data["ok"]
    assert [c[0] for c in calls] == ["layer-1-projects", "layer-2-networking"]
    assert [s["status"] for s in data["steps"]] == ["applied", "failed", "pending", "pending"]
    assert "boom" in data["steps"][1]["error"]


def test_deploy_stops_on_a_destroy_guard_refusal_with_exit_2(checkout, monkeypatch):
    calls = _record_apply(monkeypatch, refuse="layer-3-security")
    data = deploy.deploy(checkout, "dev", deploy.plan_steps(checkout, "dev", layers=[1, 2, 3, 4]), _quiet())
    assert data["exit_code"] == 2
    assert [c[0] for c in calls] == ["layer-1-projects", "layer-2-networking", "layer-3-security"]
    assert [s["status"] for s in data["steps"]][-2:] == ["refused", "pending"]


def _fake_terragrunt(monkeypatch, plans: dict[str, dict]) -> list[tuple[list[str], Path]]:
    """repo.run stand-in: ``plan``/``show``/``apply``/``destroy`` with per-unit plan documents."""
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd, *, cwd, timeout, what):
        calls.append((cmd, Path(cwd)))
        if "show" in cmd:
            unit = next((u for u in plans if Path(cwd).as_posix().endswith(u)), None)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(plans.get(unit, {})), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(repo, "run", fake_run)
    return calls


DESTROY_PLAN = {"resource_changes": [{"address": "google_x.y", "change": {"actions": ["delete"]}}]}
ADD_PLAN = {"resource_changes": [{"address": "google_x.z", "change": {"actions": ["create"]}}]}


def test_deploy_enforces_the_destroy_guard_per_unit_through_apply_unit(checkout, monkeypatch):
    calls = _fake_terragrunt(monkeypatch, {"layer-1-projects": ADD_PLAN, "layer-2-networking": DESTROY_PLAN})
    steps = deploy.plan_steps(checkout, "dev", layers=[1, 2, 3])
    data = deploy.deploy(checkout, "dev", steps, _quiet())
    assert data["exit_code"] == 2
    assert [s["status"] for s in data["steps"]] == ["applied", "refused", "pending"]
    applied = [cwd.name for cmd, cwd in calls if "apply" in cmd]
    assert applied == ["layer-1-projects"]  # never applied the destructive plan

    calls.clear()
    opts = _quiet()
    opts.allow_destroy = True
    data = deploy.deploy(checkout, "dev", steps, opts)
    assert data["ok"]
    assert [cwd.name for cmd, cwd in calls if "apply" in cmd] == [
        "layer-1-projects",
        "layer-2-networking",
        "layer-3-security",
    ]


def test_deploy_dry_run_only_plans_and_flags_destructive_units(checkout, monkeypatch):
    calls = _fake_terragrunt(monkeypatch, {"layer-2-networking": DESTROY_PLAN})
    monkeypatch.setattr(infra, "apply_unit", lambda *a, **k: pytest.fail("dry run must not apply"))
    opts = _quiet()
    opts.dry_run = True
    data = deploy.deploy(checkout, "dev", deploy.plan_steps(checkout, "dev", all_layers=True), opts)
    assert data["ok"] and data["dry_run"]
    statuses = {s["step"]: s["status"] for s in data["steps"]}
    assert statuses["layer-2-networking"] == "planned" and statuses["images"] == "dry-run"
    assert next(s for s in data["steps"] if s["step"] == "layer-2-networking")["would_refuse"] is True
    assert not any("apply" in cmd or "destroy" in cmd for cmd, _ in calls)
    planned = [cwd for cmd, cwd in calls if "plan" in cmd]
    assert planned[0] == checkout / build.CICD_UNIT
    assert [p.relative_to(checkout / "infrastructure/live/dev").as_posix() for p in planned[1:]] == [
        "layer-1-projects",
        "layer-2-networking",
        "layer-3-security",
        "layer-4-governance",
        *LAYER5_ORDER,
    ]
    verify = next(s for s in data["steps"] if s["kind"] == "verify-images")
    assert verify["commands"] == ["esmeralda build --verify --env dev"]


# ------------------------------------------------------------------------------------------
# destroy
# ------------------------------------------------------------------------------------------


def test_destroy_refuses_prd_and_needs_the_env_name(checkout):
    with pytest.raises(OpsError, match="refuses"):
        deploy.destroy(checkout, "prd", confirm="prd")
    with pytest.raises(OpsError, match="--confirm dev"):
        deploy.destroy(checkout, "dev", confirm=None)
    with pytest.raises(OpsError, match="--confirm dev"):
        deploy.destroy(checkout, "dev", confirm="prd")


def test_destroy_runs_layers_5_to_1_in_reverse_order_and_never_layer_0(checkout, monkeypatch):
    calls = _fake_terragrunt(monkeypatch, {})
    (checkout / "infrastructure/live/.local_states/dev" / W / "test-vm/terraform.tfstate").unlink()
    data = deploy.destroy(checkout, "dev", confirm="dev", progress=lambda m: None)
    assert data["ok"]
    destroyed = [cwd.relative_to(checkout / "infrastructure/live/dev").as_posix() for cmd, cwd in calls]
    expected = [u for u in reversed(LAYER5_ORDER) if not u.endswith("test-vm")]
    assert destroyed == [*expected, "layer-4-governance", "layer-3-security", "layer-2-networking", "layer-1-projects"]
    assert all(cmd[:4] == ["terragrunt", "--non-interactive", "destroy", "-auto-approve"] for cmd, _ in calls)
    assert {"step": f"{W}/test-vm", "kind": "unit", "status": "skipped", "reason": "no state"} in data["steps"]


def test_destroy_stops_at_the_first_failure(checkout, monkeypatch):
    def fake_run(cmd, *, cwd, timeout, what):
        return subprocess.CompletedProcess(cmd, 1 if Path(cwd).name == "partner-mcp" else 0, "", "Error: nope")

    monkeypatch.setattr(repo, "run", fake_run)
    data = deploy.destroy(checkout, "dev", confirm="dev", progress=lambda m: None)
    assert data["exit_code"] == 1
    statuses = [s["status"] for s in data["steps"]]
    assert statuses[:2] == ["destroyed", "failed"] and set(statuses[2:]) == {"pending"}


# ------------------------------------------------------------------------------------------
# build: same commands as the Makefile's build_image
# ------------------------------------------------------------------------------------------

S = build.BuildSettings("P", "REPO", "SA", "BUCKET")


def _gcloud(context: str, image: str, tag: str = "dev-latest") -> str:
    return (
        f"gcloud builds submit {context} --config=.cloudbuild/build-image.yaml --project=P "
        "--service-account=projects/P/serviceAccounts/SA --gcs-source-staging-dir=gs://BUCKET/source "
        f"--substitutions=_IMAGE={image},_REPOSITORY_URL=REPO,_TAG={tag},_SHA_TAG=dev-abc1234"
    )


def test_image_commands_match_what_the_makefile_ran():
    joined = {
        img.name: [" ".join(c) for c in build.image_commands(img, S, tag="dev-latest", sha="abc1234")]
        for img in build.SERVICES + build.AGENTS
    }
    assert joined["envoy-gateway"] == [_gcloud("apps/services/envoy", "envoy-gateway")]
    assert joined["legacy-dms"] == [
        "uv export --frozen --package legacy-dms --no-dev --no-emit-workspace --quiet "
        "-o apps/services/legacy-dms/requirements.lock",
        _gcloud("apps/services/legacy-dms", "legacy-dms"),
    ]
    assert joined["income-verification-api"][-1] == _gcloud(
        "apps/services/income-verification", "income-verification-api"
    )
    assert joined["corporate-email"][-1] == _gcloud("apps/services/corporate-email", "corporate-email")
    ctx = "apps/agents/cx-mortgage-orchestrator"
    assert joined["cx-mortgage-orchestrator"] == [
        f"uv export --frozen --package cx-mortgage-orchestrator --no-dev --no-emit-workspace --quiet "
        f"-o {ctx}/requirements.lock",
        f"rm -rf {ctx}/dist",
        f"uv build --package esmeralda --wheel --out-dir {ctx}/dist --quiet",
        _gcloud(ctx, "cx-mortgage-orchestrator"),
    ]
    assert joined["ai-coe-mortgage-specialist"][-1] == _gcloud(
        "apps/agents/ai-coe-mortgage-specialist", "ai-coe-mortgage-specialist"
    )


def test_select_targets_and_aliases():
    assert [[i.name for i in b] for b in build.select("images")] == [
        ["income-verification-api", "corporate-email", "legacy-dms", "envoy-gateway"],
        ["ai-coe-mortgage-specialist", "cx-mortgage-orchestrator"],
    ]
    assert [i.name for i in build.select("envoy")[0]] == ["envoy-gateway"]
    assert [i.name for i in build.select("income-verification")[0]] == ["income-verification-api"]
    with pytest.raises(OpsError, match="Unknown build target"):
        build.select("nope")


def test_resolve_cicd_dir_explicit_env_main_checkout_and_local(checkout, monkeypatch, tmp_path):
    assert build.resolve_cicd_dir(checkout) == (checkout / build.CICD_UNIT, False)
    assert build.resolve_cicd_dir(checkout, "/x/y") == (Path("/x/y"), False)
    monkeypatch.setenv(build.CICD_DIR_ENV, "rel/dir")
    assert build.resolve_cicd_dir(checkout) == (checkout / "rel/dir", False)
    monkeypatch.delenv(build.CICD_DIR_ENV)
    # a worktree without layer-0 state reads the main checkout's
    (checkout / build.CICD_STATE).unlink()
    main = tmp_path / "main"
    (main / build.CICD_STATE).parent.mkdir(parents=True)
    (main / build.CICD_STATE).write_text(_state(1, CICD_OUTPUTS))
    monkeypatch.setattr(build, "main_checkout", lambda root: main)
    assert build.resolve_cicd_dir(checkout) == (main / build.CICD_UNIT, True)


def test_build_dry_run_resolves_layer0_outputs_from_the_state(checkout, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("dry run must not run anything"))
    data = build.build_images(checkout, "services", dry_run=True, tag="t1")
    assert data["ok"] and data["sha_tag"] == "dev-abc1234"
    envoy = next(i for i in data["images"] if i["image"] == "envoy-gateway")
    assert envoy["commands"] == [
        "gcloud builds submit apps/services/envoy --config=.cloudbuild/build-image.yaml --project=demo-cicd "
        "--service-account=projects/demo-cicd/serviceAccounts/builder@demo-cicd.iam.gserviceaccount.com "
        "--gcs-source-staging-dir=gs://demo-cicd-build-source/source "
        "--substitutions=_IMAGE=envoy-gateway,_REPOSITORY_URL=us-central1-docker.pkg.dev/demo-cicd/containers,"
        "_TAG=t1,_SHA_TAG=dev-abc1234"
    ]


def test_build_fails_early_without_layer0_outputs(checkout):
    (checkout / build.CICD_STATE).unlink()
    (checkout / build.CICD_UNIT / "terragrunt.hcl").unlink()
    with pytest.raises(OpsError, match="No layer-0 CI/CD outputs"):
        build.build_images(checkout, "legacy-dms")
    dry = build.build_images(checkout, "legacy-dms", dry_run=True)
    assert "$CICD_PROJ" in dry["images"][0]["commands"][-1]


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Records build subprocesses; ``failing`` holds image names whose gcloud submit fails."""
    calls: list[list[str]] = []
    failing: set[str] = set()

    def fake(cmd, **kwargs):
        calls.append(cmd)
        code = 1 if cmd[:3] == ["gcloud", "builds", "submit"] and any(f"_IMAGE={n}," in cmd[-1] for n in failing) else 0
        return subprocess.CompletedProcess(cmd, code)

    monkeypatch.setattr(build.subprocess, "run", fake)
    monkeypatch.setattr(repo, "which", lambda name: f"/usr/bin/{name}")
    return calls, failing


def test_build_images_runs_tests_then_services_then_agents(checkout, fake_subprocess, monkeypatch):
    calls, _ = fake_subprocess
    tests: list[Path] = []
    monkeypatch.setenv("BUILD_TAG", "dev-env-tag")
    data = build.build_images(checkout, "images", test_runner=lambda root: tests.append(root) or 0)
    assert data["ok"] and tests == [checkout] and data["tests"] == "passed"
    submitted = [
        c[-1].split(",")[0].removeprefix("--substitutions=_IMAGE=") for c in calls if c[:2] == ["gcloud", "builds"]
    ]
    assert set(submitted[:4]) == {"income-verification-api", "corporate-email", "legacy-dms", "envoy-gateway"}
    assert set(submitted[4:]) == {"ai-coe-mortgage-specialist", "cx-mortgage-orchestrator"}
    assert all("_TAG=dev-env-tag" in c[-1] for c in calls if c[:2] == ["gcloud", "builds"])
    assert (checkout / build.LOG_DIR / "legacy-dms.log").is_file()


def test_build_skip_tests_and_services_never_run_tests(checkout, fake_subprocess):
    build.build_images(checkout, "services", test_runner=lambda root: pytest.fail("services need no tests"))
    build.build_images(checkout, "agents", skip_tests=True, test_runner=lambda root: pytest.fail("skipped"))


def test_build_stops_on_test_failure_and_after_a_failed_batch(checkout, fake_subprocess):
    calls, failing = fake_subprocess
    data = build.build_images(checkout, "agents", test_runner=lambda root: 2)
    assert data["exit_code"] == 2 and data["tests"] == "failed" and calls == []

    failing.add("legacy-dms")
    data = build.build_images(checkout, "images", skip_tests=True)
    assert data["exit_code"] == 1 and not data["ok"]
    assert {i["image"] for i in data["images"]} == {
        "income-verification-api",
        "corporate-email",
        "legacy-dms",
        "envoy-gateway",
    }
    assert not any("_IMAGE=cx-mortgage-orchestrator," in c[-1] for c in calls)
    failed = next(i for i in data["images"] if i["image"] == "legacy-dms")
    assert failed["status"] == "failed" and failed["failed_command"].startswith("gcloud builds submit")


# ------------------------------------------------------------------------------------------
# release: status, promote by digest, and the running-image check (gcloud mocked)
# ------------------------------------------------------------------------------------------

DEV_REPO = CICD_OUTPUTS["dev_repository_url"]
REL_REPO = CICD_OUTPUTS["release_repository_url"]
DIGESTS = {name: "sha256:" + f"{i:x}" * 64 for i, name in enumerate(build.image_names(), 1)}
WHEN = datetime.datetime(2026, 2, 3, 4, 5, 6, tzinfo=datetime.timezone.utc)


class FakeCloud:
    """Artifact Registry tags, Cloud Run services/revisions and Agent Runtime engines, in memory."""

    def __init__(self) -> None:
        self.tags: dict[str, str] = {}
        self.services: dict[str, dict] = {}
        self.revisions: dict[str, str] = {}
        self.engines: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def tag_source(self, source: str = "dev-abc1234") -> None:
        for name, digest in DIGESTS.items():
            self.tags[f"{DEV_REPO}/{name}:{source}"] = digest

    def run(self, cmd, root, *, timeout=0.0):
        self.calls.append(cmd)

        def done(code=0, out="", err=""):
            return subprocess.CompletedProcess(cmd, code, out, err)

        if cmd[:5] == ["gcloud", "artifacts", "docker", "images", "describe"]:
            digest = self.tags.get(cmd[5])
            return done(0, digest + "\n") if digest else done(1, err="NOT_FOUND")
        if cmd[:4] == ["gcloud", "container", "images", "add-tag"]:
            self.tags[cmd[-1]] = cmd[-2].split("@", 1)[1]
            return done()
        if cmd[:4] == ["gcloud", "run", "services", "describe"]:
            svc = self.services.get(cmd[4])
            return done(0, json.dumps({"status": svc})) if svc is not None else done(1, err="NOT_FOUND")
        if cmd[:4] == ["gcloud", "run", "revisions", "describe"]:
            digest = self.revisions.get(cmd[4])
            return done(0, f"{DEV_REPO}/x@{digest}\n") if digest else done(1)
        if cmd[:3] == ["gcloud", "auth", "print-access-token"]:
            return done(0, "token\n")
        raise AssertionError(f"unexpected command {cmd}")

    def http(self, url, token, *, timeout=0.0):
        assert token == "token" and url.startswith("https://us-central1-aiplatform.googleapis.com/v1beta1/")
        name = url.rsplit("/", 1)[-1]
        if name not in self.engines:
            raise urllib.error.URLError("404")
        return {"spec": {"containerSpec": {"imageUri": self.engines[name]}}}

    def add_tags(self) -> list[list[str]]:
        return [c for c in self.calls if c[:4] == ["gcloud", "container", "images", "add-tag"]]


@pytest.fixture
def cloud(monkeypatch) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setattr(release, "_run", fake.run)
    monkeypatch.setattr(release, "_http_json", fake.http)
    return fake


def _promote(root, **kwargs):
    kwargs = {"version": "v1.2.3", "source": "dev-abc1234", "verified": "2026-02-01", "echo": None, **kwargs}
    return release.promote(root, now=lambda: WHEN, **kwargs)


def test_release_images_are_the_build_images_and_each_has_one_workload():
    assert release.images() == build.image_names()
    assert sorted(w.image for w in release.WORKLOADS) == sorted(build.image_names())


def test_promote_copies_by_digest_and_writes_the_manifest(checkout, cloud):
    cloud.tag_source()
    cloud.tags[f"{REL_REPO}/legacy-dms:v1.2.3"] = DIGESTS["legacy-dms"]  # an earlier partial run
    lines: list[str] = []
    data = _promote(checkout, echo=lines.append)
    assert data["ok"] and data["manifest_changes"] == ["(new manifest)"]
    statuses = {i["image"]: i["status"] for i in data["images"]}
    assert statuses.pop("legacy-dms") == "exists" and set(statuses.values()) == {"copied"}
    assert len(cloud.add_tags()) == len(DIGESTS) - 1
    assert [
        "gcloud", "container", "images", "add-tag", "--quiet",
        f"{DEV_REPO}/envoy-gateway@{DIGESTS['envoy-gateway']}", f"{REL_REPO}/envoy-gateway:v1.2.3",
    ] in cloud.add_tags()
    assert f"✔️  legacy-dms:v1.2.3 already points at {DIGESTS['legacy-dms']} (no copy)" in lines
    written = rm.parse((checkout / "release/prd.yaml").read_text())
    assert written["images"] == DIGESTS
    assert written["version"] == "v1.2.3" and written["source_tag"] == "dev-abc1234"
    assert written["source_commit"] == "abc1234" and written["promoted_at"] == "2026-02-03T04:05:06Z"
    assert written["verified"] == {"environment": "dev", "date": "2026-02-01", "result": "PASS"}


def test_promote_again_is_a_no_op_apart_from_promoted_at(checkout, cloud):
    cloud.tag_source()
    _promote(checkout)
    before = (checkout / "release/prd.yaml").read_text()
    cloud.calls.clear()
    data = release.promote(
        checkout, version="v1.2.3", source="dev-abc1234", verified="2026-02-01", echo=None,
        now=lambda: WHEN + datetime.timedelta(days=1),
    )
    assert data["manifest_changes"] == [] and cloud.add_tags() == []
    assert {i["status"] for i in data["images"]} == {"exists"}
    after = (checkout / "release/prd.yaml").read_text()
    diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
    assert diff == [('promoted_at: "2026-02-03T04:05:06Z"', 'promoted_at: "2026-02-04T04:05:06Z"')]


def test_promote_is_all_or_nothing(checkout, cloud):
    cloud.tag_source()
    cloud.tags[f"{REL_REPO}/cx-mortgage-orchestrator:v1.2.3"] = "sha256:" + "f" * 64
    with pytest.raises(OpsError, match="already exists in the release repository.*bump the version"):
        _promote(checkout)
    assert cloud.add_tags() == [] and not (checkout / "release/prd.yaml").exists()

    del cloud.tags[f"{REL_REPO}/cx-mortgage-orchestrator:v1.2.3"]
    del cloud.tags[f"{DEV_REPO}/cx-mortgage-orchestrator:dev-abc1234"]
    with pytest.raises(OpsError, match="cx-mortgage-orchestrator:dev-abc1234 not found in the dev repository"):
        _promote(checkout)
    assert cloud.add_tags() == [] and not (checkout / "release/prd.yaml").exists()


def test_promote_rechecks_the_copy(checkout, cloud, monkeypatch):
    cloud.tag_source()
    original = cloud.run

    def lossy(cmd, root, *, timeout=0.0):
        result = original(cmd, root, timeout=timeout)
        if cmd[:4] == ["gcloud", "container", "images", "add-tag"]:
            cloud.tags[cmd[-1]] = "sha256:" + "e" * 64
        return result

    monkeypatch.setattr(release, "_run", lossy)
    with pytest.raises(OpsError, match="after the copy, expected"):
        _promote(checkout)
    assert not (checkout / "release/prd.yaml").exists()


def test_promote_dry_run_copies_and_writes_nothing(checkout, cloud):
    cloud.tag_source()
    lines: list[str] = []
    data = _promote(checkout, dry_run=True, echo=lines.append)
    assert data["dry_run"] and cloud.add_tags() == [] and not (checkout / "release/prd.yaml").exists()
    assert {i["status"] for i in data["images"]} == {"would-copy"}
    assert any("would change: (new manifest)" in line for line in lines)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"version": "1.2.3"}, "--version must look like vX.Y.Z"),
        ({"source": "dev-latest"}, "immutable build tag"),
        ({"verified": "02/01/2026"}, "--verified must be the date"),
        ({"env": "nope"}, "Unknown env"),
    ],
)
def test_promote_validates_input_before_any_call(checkout, cloud, kwargs, message):
    with pytest.raises(OpsError, match=message):
        _promote(checkout, **kwargs)
    assert cloud.calls == []


def test_release_status_with_and_without_a_manifest(checkout, cloud):
    data = release.status(checkout)
    assert data["release"] is None and data["repositories"] == {"dev": DEV_REPO, "release": REL_REPO}
    text = release.format_status(data)
    assert "<none> (units fall back to container_tag in prd/env.yaml)" in text and REL_REPO in text
    cloud.tag_source()
    _promote(checkout)
    text = release.format_status(release.status(checkout))
    assert "• Version            : v1.2.3" in text
    assert "• Source             : dev-abc1234 (abc1234)" in text
    assert f"    {'legacy-dms':<28} {DIGESTS['legacy-dms']}" in text


def _deployed(checkout: Path, cloud: FakeCloud, *, digests: dict[str, str] | None = None) -> None:
    """dev outputs for layer 1 and both agents, and every workload running ``digests``."""
    digests = digests or DIGESTS
    states = checkout / "infrastructure/live/.local_states/dev"
    projects = {"mcps_project_id": "demo-mcps", "gateway_project_id": "demo-gateway"}
    (states / "layer-1-projects/terraform.tfstate").write_text(_state(1, projects))
    for agent in ("cx-mortgage-orchestrator", "ai-coe-mortgage-specialist"):
        engine = f"projects/demo/locations/us-central1/reasoningEngines/{agent}"
        (states / f"{W}/agents/{agent}/terraform.tfstate").write_text(_state(1, {"engine_id": engine}))
        cloud.engines[agent] = f"{DEV_REPO}/{agent}@{digests[agent]}"
    for w in release.WORKLOADS:
        if w.kind == "cloud_run":
            rev = f"{w.name}-dev-00001"
            cloud.services[f"{w.name}-dev"] = {"traffic": [{"revisionName": rev, "percent": 100}]}
            cloud.revisions[rev] = digests[w.image]


def _manifest(checkout: Path, env: str, digests: dict[str, str]) -> None:
    text = rm.render(
        version="v9.9.9", source_tag="dev-abc1234", source_commit="abc1234", promoted_at=WHEN,
        verified_env="dev", verified_date="2026-02-01", verified_result="PASS", images=digests,
    )
    rm.write(checkout / "release" / f"{env}.yaml", text)


def test_verify_images_against_the_manifest(checkout, cloud):
    _deployed(checkout, cloud)
    _manifest(checkout, "dev", DIGESTS)
    lines: list[str] = []
    data = release.verify_images(checkout, "dev", echo=lines.append)
    assert data["ok"] and data["exit_code"] == 0
    assert data["expected_from"] == "release/dev.yaml (v9.9.9)"
    # envoy is not in gateways.enabled: not checked
    assert [w["workload"] for w in data["workloads"]] == [
        "corporate-email-dev", "income-verification-dev", "legacy-dms-dev",
        "cx-mortgage-orchestrator", "ai-coe-mortgage-specialist",
    ]
    assert lines[0] == "🔎 Verifying running images for dev (release/dev.yaml (v9.9.9))..."
    assert lines[1] == f"  ✅ {'corporate-email-dev':<28} {DIGESTS['corporate-email'][7:19]}"
    assert lines[-1] == "✅ All workloads run release/dev.yaml (v9.9.9)."
    describe = next(c for c in cloud.calls if c[:4] == ["gcloud", "run", "services", "describe"])
    assert describe[4:7] == ["corporate-email-dev", "--project=demo-mcps", "--region=us-central1"]


def test_verify_images_reports_mismatches_mixed_revisions_and_unlisted_images(checkout, cloud):
    with_envoy = ENV_YAML.replace("enabled    = []", 'enabled    = ["envoy"]')
    (checkout / "infrastructure/live/dev/env.yaml").write_text(with_envoy)
    _deployed(checkout, cloud)
    listed = {k: v for k, v in DIGESTS.items() if k != "envoy-gateway"}
    _manifest(checkout, "dev", listed)
    cloud.engines["ai-coe-mortgage-specialist"] = f"{DEV_REPO}/ai-coe-mortgage-specialist@sha256:{'0' * 64}"
    cloud.services["legacy-dms-dev"] = {
        "traffic": [{"revisionName": "legacy-dms-dev-00001", "percent": 50}, {"revisionName": "old", "percent": 50}]
    }
    cloud.revisions["old"] = "sha256:" + "9" * 64
    del cloud.services["corporate-email-dev"]
    lines: list[str] = []
    data = release.verify_images(checkout, "dev", echo=lines.append)
    assert not data["ok"] and data["exit_code"] == 1 and data["failures"] == 4
    by_name = {w["workload"]: w for w in data["workloads"]}
    assert by_name["legacy-dms-dev"]["running"] == "mixed"
    assert by_name["corporate-email-dev"]["running"] == ""
    assert by_name["envoy-gateway-dev"]["expected"] == "" and by_name["envoy-gateway-dev"]["running"]
    assert by_name["income-verification-dev"]["ok"] and by_name["cx-mortgage-orchestrator"]["ok"]
    assert f"running none, expected {DIGESTS['corporate-email']} (release/dev.yaml (v9.9.9))" in "\n".join(lines)
    assert "expected <not listed>" in "\n".join(lines)
    assert lines[-1] == (
        "❌ 4 workload(s) are not running release/dev.yaml (v9.9.9). Re-run 'esmeralda deploy --layer 5 --env dev'."
    )


def test_verify_images_without_a_manifest_uses_the_container_tag(checkout, cloud):
    _deployed(checkout, cloud)
    for name, digest in DIGESTS.items():
        cloud.tags[f"{DEV_REPO}/{name}:dev-latest"] = digest
    cloud.services["legacy-dms-dev"] = {"traffic": [], "latestReadyRevisionName": "legacy-dms-dev-00001"}
    data = release.verify_images(checkout, "dev", echo=None)
    assert data["ok"] and data["expected_from"] == "tag :dev-latest" and data["manifest"] is None
    assert [c[5] for c in cloud.calls if c[:3] == ["gcloud", "artifacts", "docker"]][0].endswith(":dev-latest")


def test_cli_release_and_verify(checkout, cloud, capsys):
    cloud.tag_source()
    _deployed(checkout, cloud)
    argv = ["release", "promote", "--version", "v1.0.0", "--source", "dev-abc1234", "--verified", "2026-01-02"]
    assert cli.main([*argv, "--dry-run"]) == 0
    assert not (checkout / "release/prd.yaml").exists()
    assert cli.main(argv) == 0
    assert "🎉 Promoted v1.0.0." in capsys.readouterr().out
    assert cli.main(["release", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["release"]["version"] == "v1.0.0"
    assert cli.main(["build", "--verify", "--env", "dev"]) == 1  # no dev-latest tags: nothing expected
    assert "workload(s) are not running tag :dev-latest" in capsys.readouterr().out
    _manifest(checkout, "dev", DIGESTS)
    assert cli.main(["build", "--verify", "--env", "dev", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["release", "promote", "--version", "nope", "--source", "dev-abc1234"]) == 2
    assert "--version must look like vX.Y.Z" in capsys.readouterr().err


def test_deploy_layer5_ends_with_the_running_image_check(checkout, cloud, monkeypatch):
    monkeypatch.setattr(infra, "apply_unit", lambda *a, **k: {"exit_code": 0})
    _deployed(checkout, cloud)
    _manifest(checkout, "dev", DIGESTS)
    progress: list[str] = []
    steps = deploy.plan_steps(checkout, "dev", layers=[5])
    data = deploy.deploy(checkout, "dev", steps, deploy.Options(progress=progress.append))
    verify = data["steps"][-1]
    assert data["ok"] and verify["kind"] == "verify-images" and verify["status"] == "passed"
    assert len(verify["workloads"]) == 5 and "✅ All workloads run release/dev.yaml (v9.9.9)." in progress


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------


def test_parser_new_subcommands():
    p = cli._parser()
    ns = p.parse_args(["deploy", "--layer", "1", "--layer", "2", "--env", "dev", "--allow-destroy", "--dry-run"])
    assert ns.layer == [1, 2] and ns.allow_destroy and ns.dry_run and ns.timeout == 1800.0
    ns = p.parse_args(["deploy", "entrypoint", "gateways/direct", "--timeout", "60"])
    assert ns.units == ["entrypoint", "gateways/direct"] and ns.timeout == 60.0
    ns = p.parse_args(["deploy", "--all", "--skip-build", "--skip-tests", "--json"])
    assert ns.all_layers and ns.skip_build and ns.skip_tests and ns.json
    ns = p.parse_args(["destroy", "--env", "dev", "--confirm", "dev"])
    assert ns.confirm == "dev"
    ns = p.parse_args(["build", "images", "--tag", "x", "--skip-tests", "--dry-run", "--cicd-dir", "/c"])
    assert ns.target == "images" and ns.tag == "x" and ns.skip_tests and ns.dry_run and ns.cicd_dir == "/c"
    ns = p.parse_args(["release", "promote", "--version", "v1.0.0", "--source", "dev-abc1234"])
    assert ns.release_cmd == "promote" and ns.verified is None and ns.env == "prd"
    ns = p.parse_args(["status", "--unit", "agents/x", "--output", "engine_id"])
    assert ns.output == "engine_id"


def test_cli_deploy_json_and_exit_code(checkout, monkeypatch, capsys):
    _record_apply(monkeypatch, fail="layer-1-projects")
    assert cli.main(["deploy", "--layer", "1", "--layer", "2", "--env", "dev", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [s["status"] for s in data["steps"]] == ["failed", "pending"]
    assert cli.main(["deploy", "--env", "dev"]) == 2  # nothing chosen
    assert "Choose what to deploy" in capsys.readouterr().err


def test_cli_deploy_human_summary(checkout, monkeypatch, capsys):
    _record_apply(monkeypatch)
    assert cli.main(["deploy", "entrypoint", "--env", "dev"]) == 0
    out = capsys.readouterr().out
    assert "applied" in out and "layer-5-workloads/entrypoint" in out and "finished: 1/1" in out


def test_cli_build_dry_run_and_unknown_target(checkout, capsys):
    assert cli.main(["build", "envoy", "--dry-run"]) == 0
    assert "gcloud builds submit apps/services/envoy" in capsys.readouterr().out
    assert cli.main(["build", "nope"]) == 2
    assert cli.main(["build"]) == 2


def test_cli_status_output_prints_the_raw_value(checkout, capsys):
    state = checkout / "infrastructure/live/.local_states/dev" / W / "agents/cx-mortgage-orchestrator/terraform.tfstate"
    state.write_text(_state(1, {"engine_id": "projects/p/locations/l/reasoningEngines/1"}))
    assert (
        cli.main(["status", "--env", "dev", "--unit", "agents/cx-mortgage-orchestrator", "--output", "engine_id"]) == 0
    )
    assert capsys.readouterr().out == "projects/p/locations/l/reasoningEngines/1\n"
    assert cli.main(["status", "--env", "dev", "--unit", "agents/cx-mortgage-orchestrator", "--output", "nope"]) == 1
    assert cli.main(["status", "--env", "dev", "--output", "engine_id"]) == 2


def test_build_package_builds_and_checks_the_wheel(checkout, monkeypatch):
    pkg = checkout / "packages" / "esmeralda"
    (pkg / "src" / "esmeralda").mkdir(parents=True)
    (pkg / "README.md").write_text("readme\n")
    (pkg / "pyproject.toml").write_text('[project]\nname = "esmeralda"\nversion = "0.1.0"\n')
    seen = {}

    def fake(cmd, **kwargs):
        seen["pyproject"] = (Path(cmd[2]) / "pyproject.toml").read_text()
        with zipfile.ZipFile(checkout / "dist" / "esmeralda_platform-0.1.0-py3-none-any.whl", "w") as whl:
            whl.writestr("esmeralda/cli.py", "")
            whl.writestr("esmeralda/ops/__init__.py", "")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(build.subprocess, "run", fake)
    monkeypatch.setattr(repo, "which", lambda name: f"/usr/bin/{name}")
    data = build.build_package(checkout)
    assert data["ok"] and data["wheel"] == "dist/esmeralda_platform-0.1.0-py3-none-any.whl"
    assert 'name = "esmeralda-platform"' in seen["pyproject"]
