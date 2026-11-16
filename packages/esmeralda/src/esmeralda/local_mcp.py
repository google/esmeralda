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

"""Local MCP servers for workstation runs (``esmeralda query --local-mcp``, ``make run-mcp-local``).

The single list of the repository's MCP servers and their local ports. :func:`running` starts the
servers an agent needs (the ``localhost:<port>`` URLs in its ``agent.yaml`` ``local_env``), bound to
127.0.0.1 only, waits until each answers ``/health``, and always stops the ones it started (also on
Ctrl+C or an error). Servers already answering on their port are reused and left running.
``python -m esmeralda.local_mcp`` runs all of them in the foreground until Ctrl+C.

Development helper only: agent images never call it. Standard library only.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

HOST = "127.0.0.1"
LOG_DIR = Path(".esmeralda") / "local-mcp"  # under the repo root; gitignored
START_TIMEOUT = 60.0
STOP_TIMEOUT = 10.0


@dataclass(frozen=True)
class Server:
    name: str
    package: str  # uv workspace package
    app: str  # uvicorn application, module:attribute
    app_dir: str  # relative to the repo root
    port: int

    def command(self) -> list[str]:
        return [
            "uv", "run", "--frozen", "--package", self.package,
            "uvicorn", self.app, "--app-dir", self.app_dir, "--host", HOST, "--port", str(self.port),
        ]  # fmt: skip


SERVERS: tuple[Server, ...] = (
    Server("corporate-email", "corporate-email", "main:app", "apps/services/corporate-email", 8001),
    Server("income-verification", "income-verification-api", "main:app", "apps/services/income-verification", 8002),
    Server("legacy-dms", "legacy-dms", "server:app", "apps/services/legacy-dms", 8003),
)

_LOCAL_URL_RE = re.compile(r"^https?://(?:localhost|127\.0\.0\.1):(\d+)(?:/|$)")


class LocalMcpError(RuntimeError):
    """A local MCP server could not be started."""


def servers_for(local_env: Mapping[str, str]) -> list[Server]:
    """The servers whose port a ``local_env`` URL points at (``http://localhost:<port>/...``)."""
    ports = {int(m.group(1)) for value in local_env.values() if (m := _LOCAL_URL_RE.match(str(value)))}
    return [s for s in SERVERS if s.port in ports]


def find_root(start: str | Path) -> Path:
    """The repository root above ``start``: the first directory with ``apps/services`` and a ``pyproject.toml``."""
    here = Path(start).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "apps" / "services").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise LocalMcpError(f"no Esmeralda checkout (apps/services) above {here}")


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback: never via $HTTP_PROXY


def healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with _NO_PROXY_OPENER.open(f"http://{HOST}:{port}/health", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _stop(procs: Iterable[tuple[Server, subprocess.Popen]], out: IO[str]) -> None:
    procs = [(s, p) for s, p in procs if p.poll() is None]
    if not procs:
        return
    print(f"🧹 Stopping local MCP servers: {', '.join(s.name for s, _ in procs)}", file=out, flush=True)
    for _, proc in procs:  # each server runs in its own session: stop uv and uvicorn together
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    for _, proc in procs:
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


@contextlib.contextmanager
def running(
    root: str | Path,
    servers: Iterable[Server] = SERVERS,
    *,
    timeout: float = START_TIMEOUT,
    out: IO[str] = sys.stderr,
) -> Iterator[list[Server]]:
    """Runs the block with ``servers`` up; yields the ones it started, and stops them afterwards."""
    root = Path(root)
    wanted = list(servers)
    reused = [s for s in wanted if healthy(s.port)]
    to_start = [s for s in wanted if s not in reused]
    if reused:
        print(f"ℹ️  Local MCP servers already running: {', '.join(s.name for s in reused)}", file=out, flush=True)
    procs: list[tuple[Server, subprocess.Popen]] = []
    try:
        if to_start:
            log_dir = root / LOG_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            for server in to_start:
                log = log_dir / f"{server.name}.log"
                print(f"🚀 Starting {server.name} on http://{HOST}:{server.port} (log: {log})", file=out, flush=True)
                with log.open("w") as handle:
                    proc = subprocess.Popen(
                        server.command(),
                        cwd=root,
                        stdin=subprocess.DEVNULL,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                procs.append((server, proc))
            _wait_ready(procs, root, timeout)
            print("✅ Local MCP servers ready", file=out, flush=True)
        yield [s for s, _ in procs]
    finally:
        _stop(procs, out)


def _wait_ready(procs: list[tuple[Server, subprocess.Popen]], root: Path, timeout: float) -> None:
    pending = list(procs)
    deadline = time.monotonic() + timeout
    while pending:
        for server, proc in list(pending):
            if healthy(server.port):
                pending.remove((server, proc))
            elif proc.poll() is not None:
                raise LocalMcpError(
                    f"{server.name} exited with code {proc.returncode} before answering /health "
                    f"(see {root / LOG_DIR / (server.name + '.log')})"
                )
        if pending and time.monotonic() > deadline:
            names = ", ".join(s.name for s, _ in pending)
            raise LocalMcpError(f"local MCP servers not ready after {timeout:.0f}s: {names} (logs in {root / LOG_DIR})")
        if pending:
            time.sleep(0.5)


@contextlib.contextmanager
def sigterm_as_interrupt() -> Iterator[None]:
    """Turns SIGTERM into KeyboardInterrupt for the block, so ``finally`` clauses still stop the servers."""

    def _raise(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except ValueError:  # not the main thread
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def main(argv: list[str] | None = None) -> int:
    """Runs every local MCP server in the foreground until Ctrl+C."""
    del argv
    try:
        root = find_root(Path.cwd())
        with sigterm_as_interrupt(), running(root, SERVERS, out=sys.stdout) as started:
            if not started:
                return 0
            print("Press Ctrl+C to stop.", flush=True)
            while True:
                time.sleep(3600)
    except LocalMcpError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
