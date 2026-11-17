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

"""``uv.lock``: regenerate it against public PyPI in Cloud Build, and check it offline.

* ``check`` (``esmeralda lock --check``, ``make lock-check``, the pre-push hook and ``esmeralda
  doctor``): ``uv lock --check --offline --default-index https://pypi.org/simple``. Offline and
  read-only, so it gives the same answer on every machine, whatever package index or proxy uv is
  configured with.
* ``lock`` (``esmeralda lock [PKG...]``, ``make lock [PKG=...]``): why not just ``uv lock``? On some
  machines uv goes through a package proxy that holds back new releases and rewrites every URL in
  the lock. So the resolution runs in Cloud Build (shared CI/CD project, public PyPI) on the local
  ``pyproject.toml`` files and ``uv.lock``; the new ``uv.lock`` is written back into the working
  copy for review. Nothing is committed or pushed. Project, builder service account and source
  bucket come from the layer-0 outputs (``build.resolve_cicd_dir``: a worktree reads the main
  checkout's).
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
import tomllib
from pathlib import Path
from typing import Any

from esmeralda.ops import build, repo
from esmeralda.ops.repo import OpsError

PUBLIC_INDEX = "https://pypi.org/simple"
CHECK_CMD = ["uv", "lock", "--check", "--offline", "--default-index", PUBLIC_INDEX]
UV_VERSION = "0.9.30"  # keep in sync with the uv image in apps/services/*/Dockerfile
UV_IMAGE = f"ghcr.io/astral-sh/uv:{UV_VERSION}-python3.12-bookworm-slim"
FIX_HINT = "run `make lock` (or `esmeralda lock`), commit uv.lock"

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]+(==[A-Za-z0-9.+!_-]+)?$")


# ------------------------------------------------------------------------------------------
# check
# ------------------------------------------------------------------------------------------


def check(root: Path, *, timeout: float = 60.0) -> dict[str, Any]:
    """``{"status": "ok" | "stale" | "skipped" | "error", "detail": ...}`` for ``root``'s ``uv.lock``."""
    if not (root / "uv.lock").is_file():
        return {"status": "error", "detail": "uv.lock not found in the repository root"}
    if not (root / "pyproject.toml").is_file():
        return {"status": "error", "detail": "pyproject.toml not found in the repository root"}
    if not repo.which("uv"):
        return {"status": "skipped", "detail": f"uv not found: uv.lock not checked ({repo.INSTALL_HINTS['uv']})"}
    env = repo.tool_env()
    env.pop("UV_FROZEN", None)  # --check compares the lock with pyproject.toml; frozen would skip that
    env.pop("UV_LOCKED", None)
    try:
        res = subprocess.run(
            CHECK_CMD,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "detail": f"uv lock --check failed: {exc}"}
    if res.returncode == 0:
        return {"status": "ok", "detail": "uv.lock is in sync with pyproject.toml"}
    last = (res.stderr or res.stdout).strip().splitlines()
    why = f" ({last[-1][:200]})" if last else ""
    return {"status": "stale", "detail": f"uv.lock is out of date with pyproject.toml{why}: {FIX_HINT}"}


# ------------------------------------------------------------------------------------------
# lock (Cloud Build)
# ------------------------------------------------------------------------------------------


def validate_packages(packages: list[str]) -> list[str]:
    bad = [p for p in packages if not _PACKAGE_RE.match(p)]
    if bad:
        raise OpsError(f"Invalid package spec(s): {', '.join(bad)} (expected NAME or NAME==VERSION)")
    return list(packages)


def input_files(root: Path) -> list[str]:
    """What uv needs to resolve: the workspace ``pyproject.toml`` files, ``uv.lock`` and ``README.md``."""
    try:
        members = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]["members"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise OpsError(f"Cannot read the uv workspace members from pyproject.toml: {exc}") from exc
    files = ["pyproject.toml", "uv.lock", "README.md", *(f"{m}/pyproject.toml" for m in members)]
    return [f for f in files if (root / f).is_file()]


def cloudbuild_yaml(packages: list[str], *, bucket: str, run_id: str) -> str:
    """The Cloud Build config: ``uv lock`` in the uv image; the new lock comes back as an artifact.

    The lock travels through the source bucket, not the build log: streamed build logs can arrive
    out of order and truncated, so they can't carry it. Logs go to the same bucket (``GCS_ONLY``) so
    ``gcloud builds submit`` streams them in order.
    """
    args = ["lock", *(a for p in packages for a in ("--upgrade-package", p))]
    return (
        "steps:\n"
        f'  - name: "{UV_IMAGE}"\n'
        "    entrypoint: uv\n"
        f"    args: [{', '.join(json.dumps(a) for a in args)}]\n"
        "artifacts:\n"
        "  objects:\n"
        f'    location: "gs://{bucket}/{run_id}/"\n'
        '    paths: ["uv.lock"]\n'
        f'logsBucket: "gs://{bucket}/{run_id}/logs"\n'
        "options:\n"
        "  logging: GCS_ONLY\n"
    )


def commands(settings: build.BuildSettings, work: str, run_id: str) -> list[list[str]]:
    """``gcloud builds submit`` (synchronous, streams uv's output), then fetch the new lock."""
    p = settings.project
    return [
        [
            "gcloud", "builds", "submit", work,
            f"--config={work}/cloudbuild.yaml",
            f"--project={p}",
            f"--service-account=projects/{p}/serviceAccounts/{settings.builder_sa}",
            f"--gcs-source-staging-dir=gs://{settings.source_bucket}/source",
        ],
        [
            "gcloud", "storage", "cp", "--no-user-output-enabled",
            f"gs://{settings.source_bucket}/{run_id}/uv.lock", f"{work}/uv.lock.new",
        ],
    ]  # fmt: skip


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _lock_changed(root: Path) -> bool:
    res = subprocess.run(
        ["git", "diff", "--quiet", "--", "uv.lock"], cwd=root, capture_output=True, timeout=30, check=False
    )
    return res.returncode != 0


def lock(
    root: Path,
    packages: list[str] | None = None,
    *,
    cicd_dir: str | os.PathLike | None = None,
    dry_run: bool = False,
    timeout: float = 1200.0,
) -> dict[str, Any]:
    """Regenerates ``root/uv.lock`` in Cloud Build; ``packages`` (``NAME[==VERSION]``) are also upgraded."""
    packages = validate_packages(list(packages or []))
    location, from_main = build.resolve_cicd_dir(root, cicd_dir)
    run_id = f"uvlock-{int(time.time())}-{os.getpid()}"
    data: dict[str, Any] = {"packages": packages, "cicd_dir": str(location), "cicd_from_main_checkout": from_main}

    if dry_run:
        try:
            settings = build.build_settings(location)
        except OpsError as exc:
            data["note"] = f"{exc} (placeholders shown)"
            settings = build.PLACEHOLDERS
        data.update(
            dry_run=True,
            exit_code=0,
            files=input_files(root),
            cloudbuild_yaml=cloudbuild_yaml(packages, bucket=settings.source_bucket, run_id=run_id),
            commands=[shlex.join(c) for c in commands(settings, "$WORK", run_id)],
        )
        return data

    settings = build.build_settings(location)
    if not repo.which("gcloud"):
        raise OpsError(f"gcloud not found on PATH: {repo.INSTALL_HINTS['gcloud']}")
    upgrade = f" (upgrade: {' '.join(packages)})" if packages else ""
    _say(f"🔐 Locking against public PyPI in Cloud Build ({settings.project}){upgrade}...")
    if from_main:
        _say(f"   layer-0 outputs from the main checkout: {location}")

    with tempfile.TemporaryDirectory(prefix="esmeralda-uvlock-") as work:
        for name in input_files(root):
            (Path(work) / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(root / name, Path(work) / name)
        (Path(work) / "cloudbuild.yaml").write_text(
            cloudbuild_yaml(packages, bucket=settings.source_bucket, run_id=run_id)
        )
        submit, fetch = commands(settings, work, run_id)
        try:
            # gcloud's output goes to stderr, so stdout stays clean for --json.
            code = subprocess.run(
                submit, env=repo.tool_env(), stdout=sys.stderr, stdin=subprocess.DEVNULL, timeout=timeout,
                check=False,
            ).returncode
            if code != 0:
                data.update(exit_code=code, error="Cloud Build failed (see the output above); uv.lock unchanged")
                return data
            res = repo.run(fetch, cwd=root, timeout=300, what="gcloud storage cp")
        except subprocess.TimeoutExpired as exc:
            raise OpsError(f"Cloud Build lock timed out after {timeout:.0f}s; uv.lock unchanged") from exc
        if res.returncode != 0:
            data.update(exit_code=res.returncode, error=f"could not fetch the new uv.lock: {res.stderr.strip()[-300:]}")
            return data
        shutil.move(str(Path(work) / "uv.lock.new"), root / "uv.lock")

    result = check(root)
    changed = _lock_changed(root)
    data.update(
        exit_code=0 if result["status"] in ("ok", "skipped") else 1,
        changed=changed,
        check=result,
    )
    return data
