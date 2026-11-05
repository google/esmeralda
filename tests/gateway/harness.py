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

"""Helpers for the Envoy gateway harness (issue #73).

``render_config`` renders ``infrastructure/modules/5-workloads/services/envoy/config`` with OpenTofu or
Terraform, the same code path the ``services/envoy`` module uses in production. Tests then check the
result with the real ``envoy`` binary. Needs ``envoy`` and ``tofu`` (or ``terraform``, or ``$TF_BIN``)
on PATH; no GCP access and no containers.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_MODULE = REPO_ROOT / "infrastructure/modules/5-workloads/services/envoy/config"
APEX = "esmeralda.internal"
GOOGLE_AUDIENCE = "https://us-central1-aiplatform.googleapis.com"


def tool(name: str) -> str:
    """Absolute path of a binary the harness needs; stops the session with a clear message if missing."""
    path = shutil.which(name)
    if not path:
        pytest.exit(f"tests/gateway needs `{name}` on PATH (install steps: docs/src/contributing.md)", returncode=2)
    if name == "envoy":
        _check_envoy(path)
    return path


# gcp_authn's Audience.access_token, used for the Vertex AI routes, arrived in Envoy 1.39.
MIN_ENVOY_VERSION = (1, 39, 0)


def envoy_version(banner: str) -> tuple[int, ...]:
    """(major, minor, patch) from ``envoy --version`` output, e.g. ``envoy  version: <sha>/1.40.2/...``."""
    match = re.search(r"/(\d+)\.(\d+)\.(\d+)", banner)
    if not match:
        raise ValueError(f"no version in {banner!r}")
    return tuple(int(part) for part in match.groups())


@functools.cache
def _check_envoy(path: str) -> None:
    found = envoy_version(subprocess.run([path, "--version"], capture_output=True, text=True).stdout)
    if found < MIN_ENVOY_VERSION:
        wanted, have = ".".join(map(str, MIN_ENVOY_VERSION)), ".".join(map(str, found))
        pytest.exit(f"tests/gateway needs envoy >= {wanted}, found {have} at {path}", returncode=2)


def tf_binary() -> str:
    """``$TF_BIN`` if set, else ``terraform``, else ``tofu``."""
    if os.environ.get("TF_BIN"):
        return tool(os.environ["TF_BIN"])
    for candidate in ("terraform", "tofu"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.exit("tests/gateway needs `terraform` or `tofu` on PATH (or TF_BIN)", returncode=2)


_HANDED_OUT: set[int] = set()


def free_port() -> int:
    """A free local TCP port, never one already handed out in this session.

    Several ports are picked before any server binds them, and the kernel may hand a just-released
    port out again, so remember what was given.
    """
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in _HANDED_OUT:
            _HANDED_OUT.add(port)
            return port


@dataclasses.dataclass
class RenderedConfig:
    yaml: str
    data: dict
    hostnames: list[str]
    path: pathlib.Path


def _tf(workdir: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        [tf_binary(), f"-chdir={workdir}", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "TF_IN_AUTOMATION": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"`{' '.join(args)}` failed in {workdir}:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def render_config(workdir: pathlib.Path, **inputs) -> RenderedConfig:
    """Renders the config module with ``inputs`` as module arguments and returns its outputs."""
    workdir.mkdir(parents=True, exist_ok=True)
    root = {
        "module": {"config": {"source": str(CONFIG_MODULE), **inputs}},
        "output": {
            "envoy_yaml": {"value": "${module.config.envoy_yaml}"},
            "hostnames": {"value": "${module.config.hostnames}"},
        },
    }
    (workdir / "main.tf.json").write_text(json.dumps(root, indent=2))
    _tf(workdir, "init", "-input=false", "-no-color")
    _tf(workdir, "apply", "-auto-approve", "-input=false", "-no-color")
    outputs = json.loads(_tf(workdir, "output", "-json"))
    text = outputs["envoy_yaml"]["value"]
    path = workdir / "envoy.yaml"
    path.write_text(text)
    return RenderedConfig(yaml=text, data=yaml.safe_load(text), hostnames=outputs["hostnames"]["value"], path=path)
