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

"""Guards against perpetual Terraform diffs on the Apigee units.

* Bundles: ``data "archive_file"`` zeroes mtimes but records each file's Unix mode in the zip. Two
  checkouts with different umasks (e.g. 0644 vs 0666) build different ``output_md5`` values for the
  same content, so ``detect_md5hash`` uploads a new proxy/shared flow revision and redeploys it
  depending on which checkout ran the plan. Every ``archive_file`` must pin ``output_file_mode``.
* Developer apps: ``google_apigee_developer_app.attributes`` is an ordered list and Apigee returns it
  sorted by name, so attributes declared in any other order show a reorder diff on every plan.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TERRAFORM_ROOTS = (REPO_ROOT / "infrastructure", REPO_ROOT / "tests")
APIGEE_MODULES = REPO_ROOT / "infrastructure/modules/5-workloads/apigee"
SAMPLE_BUNDLE = REPO_ROOT / "apps/apigee/sharedflows/token-quota"

ARCHIVE_RE = re.compile(r'^data\s+"archive_file"\s+"(\w+)"\s*\{', re.MULTILINE)
DEV_APP_RE = re.compile(r'^resource\s+"google_apigee_developer_app"\s+"(\w+)"\s*\{', re.MULTILINE)


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _bodies(text: str, pattern: re.Pattern[str]) -> dict[str, str]:
    """The body of every block whose header matches ``pattern``, by block label."""
    bodies = {}
    for match in pattern.finditer(text):
        depth, i = 1, match.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        bodies[match.group(1)] = text[match.end() : i - 1]
    return bodies


def _tf_files(*roots: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        p
        for root in roots
        for p in root.rglob("*.tf")
        if not {".terraform", ".terragrunt-cache"} & set(p.parts)
    )


def test_every_archive_file_pins_output_file_mode() -> None:
    found = 0
    for path in _tf_files(*TERRAFORM_ROOTS):
        for name, body in _bodies(_strip_comments(path.read_text()), ARCHIVE_RE).items():
            found += 1
            assert re.search(r'^\s*output_file_mode\s*=\s*"0[0-7]{3}"', body, re.MULTILINE), (
                f'{path.relative_to(REPO_ROOT)}: data "archive_file" "{name}" must set output_file_mode '
                '(e.g. "0644"): the zip records file modes, so without it output_md5 depends on the '
                "checkout's umask and every plan from another checkout uploads a new revision"
            )
    assert found, "no archive_file data sources found; update this guard if bundles moved"


def test_developer_app_attributes_follow_apigee_sort_order() -> None:
    found = 0
    for path in _tf_files(APIGEE_MODULES):
        for name, body in _bodies(_strip_comments(path.read_text()), DEV_APP_RE).items():
            found += 1
            where = f"{path.relative_to(REPO_ROOT)}: google_apigee_developer_app.{name}"
            static = re.findall(r'^\s*attributes\s*\{\s*name\s*=\s*"([^"]+)"', body, re.MULTILINE)
            assert static == sorted(static), (
                f"{where}: static attributes {static} must be in sorted order {sorted(static)}; Apigee "
                "returns the list sorted by name, so any other order is a reorder diff on every plan"
            )
            dynamic = re.search(r'dynamic\s+"attributes"\s*\{\s*for_each\s*=\s*(\S)', body)
            if dynamic:
                assert not static, f"{where}: mix of static and dynamic attributes blocks"
                assert dynamic.group(1) == "{", (
                    f"{where}: dynamic attributes must iterate a map (lexical key order, which matches "
                    "Apigee's sort), not a list"
                )
            assert not re.search(r"ignore_changes\s*=\s*(all\b|\[[^\]]*attributes)", body), (
                f"{where}: don't ignore attributes; owner_identity drives the key-to-identity check"
            )
    assert found, "no google_apigee_developer_app resources found; update this guard if products moved"


def _terraform_bin() -> str:
    for candidate in (
        os.environ.get("TF_BIN"),
        shutil.which("terraform"),
        str(pathlib.Path.home() / ".terraform/bin/terraform"),
        shutil.which("tofu"),
    ):
        if candidate and shutil.which(candidate):
            return candidate
    pytest.exit("tests/guards requires `terraform` or `tofu` on PATH (or TF_BIN)", returncode=2)


def _copy_with_mode(src: pathlib.Path, dst: pathlib.Path, file_mode: int, dir_mode: int) -> None:
    shutil.copytree(src, dst)
    for path in sorted(dst.rglob("*"), reverse=True):
        path.chmod(dir_mode if path.is_dir() else file_mode)
    dst.chmod(dir_mode)


def test_bundle_md5_is_independent_of_checkout_file_modes(tmp_path: pathlib.Path) -> None:
    """Same content from a 0644 and a 0666/0777 checkout: same md5 with output_file_mode, and (control)
    different md5 without it, which proves the test can see the problem it guards against."""
    _copy_with_mode(SAMPLE_BUNDLE, tmp_path / "strict", 0o644, 0o755)
    _copy_with_mode(SAMPLE_BUNDLE, tmp_path / "loose", 0o666, 0o777)
    blocks = []
    for name in ("strict", "loose"):
        for pinned in (True, False):
            label = f"{name}_{'pinned' if pinned else 'unpinned'}"
            mode = '\n  output_file_mode = "0644"' if pinned else ""
            blocks.append(
                f'data "archive_file" "{label}" {{\n  type        = "zip"\n  source_dir  = "{name}"\n'
                f'  output_path = "out/{label}.zip"{mode}\n}}\n'
                f'output "{label}" {{\n  value = data.archive_file.{label}.output_md5\n}}\n'
            )
    (tmp_path / "main.tf").write_text(
        'terraform {\n  required_providers {\n    archive = {\n      source  = "hashicorp/archive"\n'
        '      version = ">= 2.4"\n    }\n  }\n}\n' + "".join(blocks)
    )
    tf = _terraform_bin()
    for args in (["init", "-input=false", "-no-color"], ["apply", "-auto-approve", "-input=false", "-no-color"]):
        res = subprocess.run([tf, f"-chdir={tmp_path}", *args], capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"terraform {args[0]} failed:\n{res.stdout}\n{res.stderr}"
    res = subprocess.run([tf, f"-chdir={tmp_path}", "output", "-json"], capture_output=True, text=True, check=True)
    md5 = {k: v["value"] for k, v in json.loads(res.stdout).items()}
    assert md5["strict_unpinned"] != md5["loose_unpinned"], "control: file modes no longer change the zip md5"
    assert md5["strict_pinned"] == md5["loose_pinned"], (
        "output_file_mode must make the bundle md5 depend only on content, not on the checkout's file modes"
    )
