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

"""Layer 5 workloads deploy container images by digest, never straight from a tag variable.

Cloud Run / Agent Runtime resolve a tag only when a revision is created, so a template that keeps
saying ``:dev-latest`` never rolls out a rebuilt image. Workloads must deploy the digest resolved at
plan time (``local.image_by_digest`` in ``services/*/image.tf``, the ``docker_image`` data source in
``agents/*``). Formerly ``scripts/check_image_pinning.sh``.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKLOADS_DIR = REPO_ROOT / "infrastructure/modules/5-workloads"
SKIP_DIRS = {".terraform", ".terragrunt-cache"}

IMAGE_FROM_VARIABLE_RE = re.compile(r"^\s*(image|image_uri)\s*=\s*var\.")


def _workload_tf_files() -> list[pathlib.Path]:
    return sorted(
        path
        for path in WORKLOADS_DIR.rglob("*.tf")
        if not SKIP_DIRS.intersection(path.relative_to(WORKLOADS_DIR).parts)
    )


def test_workload_modules_exist():
    assert _workload_tf_files(), f"no Terraform files found under {WORKLOADS_DIR.relative_to(REPO_ROOT)}"


def test_workload_images_are_deployed_by_digest():
    matches = [
        f"{path.relative_to(REPO_ROOT)}:{lineno}:{line}"
        for path in _workload_tf_files()
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if IMAGE_FROM_VARIABLE_RE.search(line)
    ]
    assert not matches, (
        "Workload images must be deployed by digest, not by tag variable:\n"
        + "\n".join(matches)
        + "\nUse local.image_by_digest (copy services/corporate-email/image.tf)."
    )


def test_pattern_catches_tag_variables():
    assert IMAGE_FROM_VARIABLE_RE.search("      image = var.container_image")
    assert IMAGE_FROM_VARIABLE_RE.search("  image_uri=var.image_uri")
    assert not IMAGE_FROM_VARIABLE_RE.search("      image = local.image_by_digest")
    assert not IMAGE_FROM_VARIABLE_RE.search("  image_uri = data.docker_image.agent.repo_digest")
    assert not IMAGE_FROM_VARIABLE_RE.search('  description = "image = var.x is not allowed"')
