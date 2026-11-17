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

"""The private test VM: where it is, what it reaches, and running a bash payload on it.

Hosts under the private domain only resolve inside the Shared VPC, so live probes run on the test
VM over ``gcloud compute ssh --tunnel-through-iap``. ``resolve(root, env)`` reads, from the unit
outputs (nothing hardcoded):

* the VM name, zone and project (``layer-5-workloads/test-vm``, with a one-release fallback to the
  legacy ``layer-5-workloads/services/test-vm``);
* the entry point IP and private domain (``layer-2-networking``);
* the internal Root CA certificate (public; Secret Manager secret from ``layer-3-security``).

``run(vm, payload, vars=..., files=...)`` sends ``payload`` (bash) to the VM on stdin, after one
shell-quoted ``NAME=value`` line per variable and one ``<NAME>_B64=<base64>`` line per file. The VM
has no checkout or virtual environment: everything the payload needs travels in it. Secrets go
through stdin, never on a command line.
"""

from __future__ import annotations

import base64
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
from esmeralda.ops.repo import OpsError

VM_UNITS = ("layer-5-workloads/test-vm", "layer-5-workloads/services/test-vm")
NETWORKING_UNIT = "layer-2-networking"
SECURITY_UNIT = "layer-3-security"


@dataclass(frozen=True)
class TestVM:
    """The resolved test VM and the entry point it probes."""

    __test__ = False  # not a pytest test class

    env: str
    name: str
    zone: str
    project: str
    entry_ip: str
    domain: str
    ca_pem: str


def outputs(root: Path, env: str, unit: str) -> dict[str, Any]:
    """Non-sensitive outputs of ``unit``; empty when the unit doesn't exist or has no state."""
    try:
        return repo.unit_state(root, env, unit).outputs
    except OpsError:
        return {}


def text(value: Any) -> str:
    """An output as ``terragrunt output -raw`` prints it ("" for null, ``true``/``false`` for bools)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def secret(secret_id: str, *, timeout: float = 60.0) -> str:
    """Latest version of ``projects/<project>/secrets/<name>`` (gcloud wants the short name + --project)."""
    parts = secret_id.split("/")
    if len(parts) < 4 or parts[0] != "projects" or parts[2] != "secrets":
        raise OpsError(f"Not a Secret Manager secret id: {secret_id}")
    cmd = ["gcloud", "secrets", "versions", "access", "latest", f"--secret={parts[3]}", f"--project={parts[1]}"]
    result = repo.run(cmd, cwd=Path.cwd(), timeout=timeout, what="gcloud secrets versions access")
    if result.returncode != 0:
        raise OpsError(f"Could not read secret {parts[3]}: {' | '.join(repo.error_lines(result.stderr, 3))}")
    return result.stdout.removesuffix("\n")


def env_rel(env: str, *parts: str) -> str:
    return "/".join([str(repo.LIVE), env, *parts])


def vm_location(root: Path, env: str) -> tuple[str, str, str]:
    """``(name, zone, project)`` of the test VM."""
    vm_dir = env_rel(env, VM_UNITS[0])
    for unit in VM_UNITS:
        found = outputs(root, env, unit)
        name, zone, project = (text(found.get(k)) for k in ("instance_name", "instance_zone", "project_id"))
        if name:
            break
    if not (name and zone):
        raise OpsError(f"No test VM outputs in {vm_dir}. Deploy it first (make deploy-workloads ENV={env}).")
    if not project:
        raise OpsError(f"No project_id output in {vm_dir}. Re-apply the test-vm unit to publish it.")
    return name, zone, project


def resolve(root: Path, env: str) -> TestVM:
    """The test VM, the entry point IP and domain, and the internal Root CA (raises OpsError)."""
    repo.env_dir(root, env)
    name, zone, project = vm_location(root, env)
    networking = outputs(root, env, NETWORKING_UNIT)
    entry_ip, domain = text(networking.get("entrypoint_address")), text(networking.get("entrypoint_domain"))
    if not (entry_ip and domain):
        raise OpsError(f"No entry point address in layer 2. Run make deploy-networking ENV={env}.")
    ca_secret = text(outputs(root, env, SECURITY_UNIT).get("internal_ca_secret_id"))
    if not ca_secret:
        raise OpsError("No internal_ca_secret_id output in layer 3.")
    return TestVM(env, name, zone, project, entry_ip, domain, secret(ca_secret))


def payload(script: str, *, vars: dict[str, str] | None = None, files: dict[str, Path] | None = None) -> str:
    """``script`` prefixed with one shell-quoted assignment per variable and per base64-encoded file."""
    lines = [f"{key}={shlex.quote(str(value))}" for key, value in (vars or {}).items()]
    for key, path in (files or {}).items():
        lines.append(f"{key}_B64={base64.b64encode(Path(path).read_bytes()).decode('ascii')}")
    return "\n".join([*lines, script])


def ssh_cmd(vm: TestVM, command: str = "bash -s") -> list[str]:
    return [
        "gcloud",
        "compute",
        "ssh",
        vm.name,
        f"--zone={vm.zone}",
        f"--project={vm.project}",
        "--tunnel-through-iap",
        "--quiet",
        f"--command={command}",
    ]


def run(
    vm: TestVM,
    script: str,
    *,
    vars: dict[str, str] | None = None,
    files: dict[str, Path] | None = None,
    timeout: float = 300.0,
    echo: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Runs a bash payload on the test VM over IAP SSH; output captured (and printed live with ``echo``)."""
    if not repo.which("gcloud"):
        raise OpsError(f"gcloud not found on PATH: {repo.INSTALL_HINTS.get('gcloud', 'install gcloud')}")
    body = payload(script, vars=vars, files=files)
    cmd = ssh_cmd(vm)
    if not echo:
        try:
            return subprocess.run(cmd, input=body, capture_output=True, text=True, timeout=timeout, env=repo.tool_env())
        except subprocess.TimeoutExpired as exc:
            raise OpsError(f"the test VM run timed out after {timeout:.0f}s on {vm.name}") from exc
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=repo.tool_env(),
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(body)
    proc.stdin.close()
    out: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        out.append(line)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise OpsError(f"the test VM run timed out after {timeout:.0f}s on {vm.name}") from exc
    return subprocess.CompletedProcess(cmd, code, "".join(out), "")
