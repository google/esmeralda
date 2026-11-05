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
import time

import httpx
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


class EnvoyProcess:
    """Runs ``envoy`` with a rendered config while the context is open; waits for admin ``/ready``.

    One worker thread (``--concurrency 1``) keeps per-worker state such as the token cache deterministic.
    """

    def __init__(self, config: RenderedConfig, log_file: pathlib.Path):
        self.config = config
        self.log_file = log_file
        self.admin_port = config.data["admin"]["address"]["socket_address"]["port_value"]
        self.listener_port = config.data["static_resources"]["listeners"][0]["address"]["socket_address"]["port_value"]
        self._proc: subprocess.Popen | None = None
        self._log = None

    def __enter__(self) -> EnvoyProcess:
        self._log = self.log_file.open("w")
        self._proc = subprocess.Popen(
            [tool("envoy"), "-c", str(self.config.path), "--disable-hot-restart", "--concurrency", "1"],
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._log.close()
                raise AssertionError(f"envoy exited with {self._proc.returncode}:\n{self.log_file.read_text()}")
            try:
                ready = httpx.get(f"http://127.0.0.1:{self.admin_port}/ready", timeout=1, trust_env=False)
                if ready.status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.__exit__()
        raise AssertionError(f"envoy not ready after 30 s:\n{self.log_file.read_text()}")

    def __exit__(self, *exc) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        if self._log:
            self._log.close()

    def counter(self, name: str) -> int:
        """Value of one counter from the admin ``/stats`` endpoint (0 if absent)."""
        stats = f"http://127.0.0.1:{self.admin_port}/stats"
        text = httpx.get(stats, params={"filter": f"^{name}$"}, timeout=5, trust_env=False).text
        for line in text.splitlines():
            stat, _, value = line.partition(": ")
            if stat == name:
                return int(value)
        return 0


@dataclasses.dataclass
class RawResponse:
    status: int
    body: bytes


class Gateway:
    """Client for an ``EnvoyProcess``: requests go to its listener with the given ``Host`` header.

    Proxy settings from the environment are ignored: every request is for 127.0.0.1.
    """

    def __init__(self, envoy: EnvoyProcess):
        self.envoy = envoy
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{envoy.listener_port}", timeout=10, trust_env=False)

    def request(self, method: str, host: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Host": host, **kwargs.pop("headers", {})}
        return self._client.request(method, path, headers=headers, **kwargs)

    def get(self, host: str, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", host, path, **kwargs)

    def post(self, host: str, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", host, path, **kwargs)

    def stream(self, method: str, host: str, path: str, **kwargs):
        headers = {"Host": host, **kwargs.pop("headers", {})}
        return self._client.stream(method, path, headers=headers, **kwargs)

    def raw(self, method: str, host: str, path: str, headers: list[tuple[str, str]] | None = None) -> RawResponse:
        """Sends ``path`` exactly as given (httpx would normalize it) over a fresh connection."""
        lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Content-Length: 0", "Connection: close"]
        lines += [f"{name}: {value}" for name, value in headers or []]
        with socket.create_connection(("127.0.0.1", self.envoy.listener_port), timeout=10) as sock:
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        return RawResponse(status=int(head.split(b" ", 2)[1]), body=body)

    def close(self) -> None:
        self._client.close()


def virtual_hosts(config: RenderedConfig) -> list[dict]:
    """The virtual hosts of the gateway listener's route config."""
    manager = config.data["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]
    return manager["route_config"]["virtual_hosts"]


def proxied_routes(config: RenderedConfig) -> list[tuple[str, dict]]:
    """(virtual host name, route) for every route that proxies to a cluster (not direct responses)."""
    return [(vhost["name"], route) for vhost in virtual_hosts(config) for route in vhost["routes"] if "route" in route]
