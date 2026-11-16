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

"""Image builds (Cloud Build), the image check, the package build, and release promotion.

* ``build_images`` submits each image to Cloud Build in the shared CI/CD project, pushed to the
  shared dev repository as ``:<tag>`` (default ``dev-latest``) and ``:dev-<gitsha>``. The project,
  repository, builder service account and source bucket come from the layer-0 outputs.
* ``verify_images`` runs ``scripts/verify_images.sh`` (running digest == expected digest).
* ``release`` runs ``scripts/promote_release.sh`` (promotion by digest + ``release/<env>.yaml``).
* ``build_package`` builds the standalone ``esmeralda-platform`` wheel and sdist into ``dist/``.

Layer-0 resolution (``resolve_cicd_dir``): with the local backend the layer-0 state lives in the
checkout that applied it, so a git worktree has none. When this checkout has no layer-0 state and
the main checkout (``git rev-parse --git-common-dir``) has it, outputs are read from the main
checkout. An explicit ``--cicd-dir`` (or ``$CICD_DIR``) always wins.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
from esmeralda.ops.repo import OpsError

CICD_UNIT = Path("infrastructure/live/shared/layer-0-cicd")
CICD_STATE = Path("infrastructure/live/.local_states/shared/layer-0-cicd/terraform.tfstate")
CICD_DIR_ENV = "CICD_DIR"
BUILD_CONFIG = ".cloudbuild/build-image.yaml"
DEFAULT_TAG = "dev-latest"
LOG_DIR = Path(".esmeralda/build-logs")  # under the repo root; gitignored
TEST_TARGETS = ("lock-check", "test", "test-terraform")  # what `make build-agents` runs first


@dataclass(frozen=True)
class Image:
    """One image: build context, image name, uv package to lock (optional), library wheel (optional)."""

    name: str
    context: str
    package: str | None = None
    wheel: str | None = None
    aliases: tuple[str, ...] = ()


# Same order as the Makefile's build-services / build-agents targets.
SERVICES = (
    Image(
        "income-verification-api",
        "apps/services/income-verification",
        "income-verification-api",
        aliases=("income-verification", "service-income-verification"),
    ),
    Image("corporate-email", "apps/services/corporate-email", "corporate-email", aliases=("service-corporate-email",)),
    Image("legacy-dms", "apps/services/legacy-dms", "legacy-dms", aliases=("service-legacy-dms",)),
    Image("envoy-gateway", "apps/services/envoy", aliases=("envoy", "service-envoy")),
)
AGENTS = (
    Image(
        "ai-coe-mortgage-specialist",
        "apps/agents/ai-coe-mortgage-specialist",
        "ai-coe-mortgage-specialist",
        "esmeralda",
    ),
    Image("cx-mortgage-orchestrator", "apps/agents/cx-mortgage-orchestrator", "cx-mortgage-orchestrator", "esmeralda"),
)
GROUPS = {"services": SERVICES, "agents": AGENTS, "images": SERVICES + AGENTS}
TARGETS = ("agents", "services", "images", "package")


def image_names() -> list[str]:
    return [img.name for img in SERVICES + AGENTS]


def select(target: str) -> list[list[Image]]:
    """The images to build for ``target``, as batches (built concurrently within a batch)."""
    if target == "images":
        return [list(SERVICES), list(AGENTS)]
    if target in GROUPS:
        return [list(GROUPS[target])]
    for img in SERVICES + AGENTS:
        if target == img.name or target in img.aliases:
            return [[img]]
    raise OpsError(
        f"Unknown build target '{target}'. Use one of: {', '.join(TARGETS)}, or an image: {', '.join(image_names())}"
    )


# ------------------------------------------------------------------------------------------
# layer-0 outputs
# ------------------------------------------------------------------------------------------


def main_checkout(root: Path) -> Path | None:
    """The main checkout of ``root``'s repository (``root`` itself unless it is a git worktree)."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    common = Path(res.stdout.strip())
    if not common.is_absolute():
        common = root / common
    return common.resolve().parent


def resolve_cicd_dir(root: Path, override: str | os.PathLike | None = None) -> tuple[Path, bool]:
    """``(layer-0 unit directory, read_from_main_checkout)``, as the Makefile resolved ``CICD_DIR``."""
    explicit = override or os.environ.get(CICD_DIR_ENV)
    if explicit:
        path = Path(explicit).expanduser()
        return (path if path.is_absolute() else (root / path)), False
    if not (root / CICD_STATE).is_file():
        main = main_checkout(root)
        if main and main != root.resolve() and (main / CICD_STATE).is_file():
            return main / CICD_UNIT, True
    return root / CICD_UNIT, False


def cicd_outputs(cicd_dir: Path, *, timeout: float = 180.0) -> dict[str, Any]:
    """Layer-0 outputs: the local state file next to ``cicd_dir`` if present, else ``terragrunt output -json``."""
    state = cicd_dir.parent.parent / ".local_states" / "shared" / cicd_dir.name / "terraform.tfstate"
    if state.is_file():
        try:
            return repo.parse_state(cicd_dir.name, json.loads(state.read_text())).outputs
        except (OSError, json.JSONDecodeError) as exc:
            raise OpsError(f"Unreadable layer-0 state {state}: {exc}") from exc
    if not (cicd_dir / repo.UNIT_FILE).is_file():
        return {}
    result = repo.run(repo.terragrunt_cmd("output", "-json"), cwd=cicd_dir, timeout=timeout, what="terragrunt output")
    if result.returncode != 0:
        return {}
    try:
        return repo.parse_output_json(cicd_dir.name, json.loads(result.stdout or "{}")).outputs
    except json.JSONDecodeError:
        return {}


@dataclass(frozen=True)
class BuildSettings:
    project: str
    repository_url: str
    builder_sa: str
    source_bucket: str


PLACEHOLDERS = BuildSettings("$CICD_PROJ", "$REPO_URL", "$BUILDER_SA", "$SOURCE_BUCKET")


def build_settings(cicd_dir: Path) -> BuildSettings:
    outputs = cicd_outputs(cicd_dir)
    project = outputs.get("cicd_project_id")
    if not project:
        raise OpsError(
            f"No layer-0 CI/CD outputs in {cicd_dir} (no state there?). Deploy layer 0 "
            f"(esmeralda deploy --layer 0), or pass --cicd-dir <checkout with layer-0 state>/{CICD_UNIT.as_posix()}."
        )
    return BuildSettings(
        project=str(project),
        repository_url=str(outputs.get("dev_repository_url")),
        builder_sa=str(outputs.get("builder_sa_email")),
        source_bucket=str(outputs.get("build_source_bucket")),
    )


# ------------------------------------------------------------------------------------------
# commands
# ------------------------------------------------------------------------------------------


def git_sha(root: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else "unknown"


def image_commands(img: Image, settings: BuildSettings, *, tag: str, sha: str) -> list[list[str]]:
    """The commands ``make build-<image>`` ran, in order (run from the repo root)."""
    commands: list[list[str]] = []
    if img.package:
        commands.append(
            [
                "uv",
                "export",
                "--frozen",
                "--package",
                img.package,
                "--no-dev",
                "--no-emit-workspace",
                "--quiet",
                "-o",
                f"{img.context}/requirements.lock",
            ]
        )
    if img.wheel:
        commands.append(["rm", "-rf", f"{img.context}/dist"])
        commands.append(
            ["uv", "build", "--package", img.wheel, "--wheel", "--out-dir", f"{img.context}/dist", "--quiet"]
        )
    p = settings.project
    commands.append(
        [
            "gcloud",
            "builds",
            "submit",
            img.context,
            f"--config={BUILD_CONFIG}",
            f"--project={p}",
            f"--service-account=projects/{p}/serviceAccounts/{settings.builder_sa}",
            f"--gcs-source-staging-dir=gs://{settings.source_bucket}/source",
            f"--substitutions=_IMAGE={img.name},_REPOSITORY_URL={settings.repository_url},_TAG={tag},_SHA_TAG=dev-{sha}",
        ]
    )
    return commands


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _run_logged(cmd: list[str], *, cwd: Path, log, timeout: float) -> int:
    """Runs ``cmd`` with output appended to ``log``; ``rm -rf`` runs in-process."""
    log.write(f"$ {shlex.join(cmd)}\n")
    log.flush()
    if cmd[:2] == ["rm", "-rf"]:
        for target in cmd[2:]:
            shutil.rmtree(cwd / target, ignore_errors=True)
        return 0
    if not repo.which(cmd[0]):
        raise OpsError(f"{cmd[0]} not found on PATH: {repo.INSTALL_HINTS.get(cmd[0], f'install {cmd[0]}')}")
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=repo.tool_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        ).returncode
    except subprocess.TimeoutExpired as exc:
        raise OpsError(f"{cmd[0]} timed out after {timeout:.0f}s") from exc


def _tail(path: Path, lines: int = 15) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def _build_one(root: Path, img: Image, commands: list[list[str]], *, timeout: float) -> dict[str, Any]:
    log_path = root / LOG_DIR / f"{img.name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result: dict[str, Any] = {"image": img.name, "log": str(log_path.relative_to(root))}
    try:
        with log_path.open("w") as log:
            for cmd in commands:
                code = _run_logged(cmd, cwd=root, log=log, timeout=timeout)
                if code != 0:
                    result.update(status="failed", exit_code=code, failed_command=shlex.join(cmd))
                    break
            else:
                result.update(status="built", exit_code=0)
    except OpsError as exc:
        result.update(status="failed", exit_code=1, error=str(exc))
    result["seconds"] = round(time.monotonic() - started, 1)
    if result["status"] == "failed":
        result["tail"] = _tail(log_path)
    return result


def run_tests(root: Path) -> int:
    """``make lock-check test test-terraform`` (what ``make build-agents`` runs before building)."""
    _say(f"🧪 Running tests before the agent builds: make {' '.join(TEST_TARGETS)} (--skip-tests to skip)")
    return subprocess.run(
        ["make", "--no-print-directory", *TEST_TARGETS], cwd=root, env=repo.tool_env(), check=False
    ).returncode


def build_images(
    root: Path,
    target: str,
    *,
    tag: str | None = None,
    cicd_dir: str | os.PathLike | None = None,
    skip_tests: bool = False,
    dry_run: bool = False,
    timeout: float = 3600.0,
    jobs: int | None = None,
    test_runner: Callable[[Path], int] = run_tests,
) -> dict[str, Any]:
    """Builds ``target`` (``agents``, ``services``, ``images`` or one image) with Cloud Build.

    Images in a batch build concurrently (``jobs``, default: the batch size, as ``make -j``);
    ``images`` builds the services, then the agents. Agent builds run the offline tests first
    unless ``skip_tests``. Stops after the first batch with a failure.
    """
    batches = select(target)
    tag = tag or os.environ.get("BUILD_TAG") or DEFAULT_TAG
    sha = git_sha(root)
    location, from_main = resolve_cicd_dir(root, cicd_dir)
    result: dict[str, Any] = {
        "target": target,
        "tag": tag,
        "sha_tag": f"dev-{sha}",
        "cicd_dir": str(location),
        "dry_run": dry_run,
        "images": [],
    }
    if from_main:
        _say(f"ℹ️  No layer-0 state in this worktree: reading CI/CD outputs from {location}")
    try:
        settings = build_settings(location)
    except OpsError as exc:
        if not dry_run:
            raise
        _say(f"⚠️  {exc} (dry run: showing placeholders)")
        settings = PLACEHOLDERS
    plans = [[(img, image_commands(img, settings, tag=tag, sha=sha)) for img in batch] for batch in batches]

    if dry_run:
        for batch in plans:
            for img, commands in batch:
                result["images"].append(
                    {"image": img.name, "status": "dry-run", "commands": [shlex.join(c) for c in commands]}
                )
        result.update(ok=True, exit_code=0)
        return result

    needs_tests = any(img.wheel for batch in batches for img in batch)
    if needs_tests and not skip_tests:
        code = test_runner(root)
        result["tests"] = "passed" if code == 0 else "failed"
        if code != 0:
            result.update(ok=False, exit_code=code)
            return result
    for batch in plans:
        names = ", ".join(img.name for img, _ in batch)
        _say(f"🏗️  Building {names} (:{tag}, :dev-{sha}) in the shared CI/CD project (logs in {LOG_DIR}/)...")
        workers = max(1, min(jobs or len(batch), len(batch)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            done = list(pool.map(lambda item: _build_one(root, item[0], item[1], timeout=timeout), batch))
        for item in done:
            icon = "✅" if item["status"] == "built" else "❌"
            _say(f"  {icon} {item['image']} ({item['seconds']}s, log {item['log']})")
        result["images"].extend(done)
        if any(item["status"] != "built" for item in done):
            result.update(ok=False, exit_code=1)
            return result
    result.update(ok=True, exit_code=0)
    return result


# ------------------------------------------------------------------------------------------
# scripts: image check and release promotion (output passes straight through)
# ------------------------------------------------------------------------------------------


def _script(root: Path, args: list[str]) -> int:
    if not repo.which("bash"):
        raise OpsError(f"bash not found on PATH: {repo.INSTALL_HINTS['bash']}")
    return subprocess.run(["bash", *args], cwd=root, env=repo.tool_env(), check=False).returncode


def verify_images_cmd(env: str) -> list[str]:
    return ["bash", "scripts/verify_images.sh", env]


def verify_images(root: Path, env: str) -> int:
    """``scripts/verify_images.sh <env>``: every Layer 5 workload runs the expected digest."""
    repo.env_dir(root, env)
    return _script(root, verify_images_cmd(env)[1:])


def release_cmd(
    action: str, *, env: str = "prd", version: str | None = None, source: str | None = None, verified: str | None = None
) -> list[str]:
    cmd = ["bash", "scripts/promote_release.sh", action]
    if action == "promote":
        cmd += ["--version", version or "", "--source", source or ""]
        if verified:
            cmd += ["--verified", verified]
    if env != "prd":
        cmd += ["--env", env]
    return cmd


def release(root: Path, action: str, **kwargs: Any) -> int:
    """``scripts/promote_release.sh status|promote ...`` (same output and ``release/<env>.yaml``)."""
    if action not in ("status", "promote"):
        raise OpsError(f"Unknown release action '{action}'")
    return _script(root, release_cmd(action, **kwargs)[1:])


# ------------------------------------------------------------------------------------------
# package: the standalone esmeralda-platform wheel and sdist
# ------------------------------------------------------------------------------------------


def build_package(root: Path, *, out_dir: str = "dist", dry_run: bool = False) -> dict[str, Any]:
    """Builds ``esmeralda-platform`` (the ``esmeralda`` package renamed) into ``dist/`` and checks the wheel."""
    import zipfile

    from esmeralda.ops import guard

    pkg = root / "packages" / "esmeralda"
    out = root / out_dir
    cmd = ["uv", "build", "<tmp>", "--wheel", "--sdist", "--out-dir", out_dir, "--offline", "--quiet"]
    if dry_run:
        return {"target": "package", "dry_run": True, "commands": [shlex.join(cmd)], "ok": True, "exit_code": 0}
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(pkg / "src", Path(tmp) / "src")
        shutil.copy2(pkg / "README.md", Path(tmp) / "README.md")
        text = (pkg / "pyproject.toml").read_text()
        (Path(tmp) / "pyproject.toml").write_text(
            re.sub(r'^name = "esmeralda"', 'name = "esmeralda-platform"', text, flags=re.MULTILINE)
        )
        cmd[2] = tmp
        if not repo.which("uv"):
            raise OpsError(f"uv not found on PATH: {repo.INSTALL_HINTS['uv']}")
        code = subprocess.run(cmd, cwd=root, env=repo.tool_env(), check=False).returncode
        if code != 0:
            return {"target": "package", "ok": False, "exit_code": code}
    wheels = sorted(out.glob("esmeralda_platform-*.whl"))
    if not wheels:
        raise OpsError(f"No esmeralda_platform wheel in {out_dir}/")
    names = zipfile.ZipFile(wheels[0]).namelist()
    problems = []
    if "esmeralda/cli.py" not in names or not any(n.startswith("esmeralda/ops/") for n in names):
        problems.append("wheel is missing esmeralda/cli.py or esmeralda/ops/")
    problems += [f"{f.where}: {f.rule}" for n in names for f in guard.check_path(n)]
    return {
        "target": "package",
        "wheel": str(wheels[0].relative_to(root)),
        "files": names,
        "artifacts": sorted(str(p.relative_to(root)) for p in out.iterdir()),
        "problems": problems,
        "ok": not problems,
        "exit_code": 0 if not problems else 1,
    }
