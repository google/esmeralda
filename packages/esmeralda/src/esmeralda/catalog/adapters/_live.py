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

"""Live reads for the built-in adapters: unit outputs and read-only ``gcloud`` calls.

Used only by ``catalog fetch`` (and ``catalog diff --live``), never by ``resolve``. Tests replace
these functions with recorded responses, so no test touches the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from esmeralda.catalog.schema import CatalogError
from esmeralda.catalog.snapshot import SourceUnreachable


def unit_outputs(root: Path, env: str, unit: str) -> dict[str, Any]:
    """Non-sensitive outputs of a deployed unit (local state or ``terragrunt output``)."""
    from esmeralda.ops import repo
    from esmeralda.ops.repo import OpsError

    try:
        return dict(repo.unit_state(root, env, unit).outputs)
    except OpsError as exc:
        raise SourceUnreachable(f"outputs of {unit} ({env}): {exc}") from exc


def env_region(root: Path, env: str) -> str:
    """The environment's region (``infrastructure/live/<env>/env.yaml``)."""
    from esmeralda.ops import repo
    from esmeralda.ops.repo import OpsError

    try:
        return repo._hcl_string(repo.env_locals(root, env), "region")
    except (OpsError, OSError) as exc:
        raise SourceUnreachable(f"region of {env}: {exc}") from exc


def gcloud_json(root: Path, args: list[str], what: str) -> Any:
    """``gcloud <args> --format=json`` (read-only calls only); :class:`SourceUnreachable` on failure."""
    from esmeralda.ops import repo
    from esmeralda.ops.repo import OpsError

    try:
        result = repo.run(["gcloud", *args, "--format=json"], cwd=root, timeout=120, what=what)
    except OpsError as exc:
        raise SourceUnreachable(f"{what}: {exc}") from exc
    if result.returncode != 0:
        raise SourceUnreachable(f"{what} failed: {repo.short(result.stderr, 300)}")
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise SourceUnreachable(f"{what}: unreadable gcloud output ({exc})") from exc


_SECRET = re.compile(r"^projects/(?P<project>[^/]+)/secrets/(?P<secret>[^/]+)(?:/versions/(?P<version>[^/]+))?$")


def secret_text(root: Path, resource: str, what: str) -> str:
    """The value of a Secret Manager secret version (``projects/<p>/secrets/<s>[/versions/<v>]``,
    default ``latest``) through a read-only ``gcloud secrets versions access``.

    The value is returned only: never logged, and error messages carry gcloud's stderr, never stdout.
    """
    from esmeralda.ops import repo
    from esmeralda.ops.repo import OpsError

    match = _SECRET.match(resource)
    if not match:
        raise CatalogError(f"{what}: '{resource}' is not a secret resource name "
                           "(projects/<project>/secrets/<secret>[/versions/<version>])")
    cmd = ["gcloud", "secrets", "versions", "access", match["version"] or "latest", f"--secret={match['secret']}",
           f"--project={match['project']}"]
    try:
        result = repo.run(cmd, cwd=root, timeout=60, what=what)
    except OpsError as exc:
        raise SourceUnreachable(f"{what}: {exc}") from None
    if result.returncode != 0:
        raise SourceUnreachable(f"{what} failed: {repo.short(result.stderr, 300)}")
    return (result.stdout or "").strip()
