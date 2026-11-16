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

"""esmeralda.local_mcp and ``esmeralda query --local-mcp``: start, readiness and teardown of the
local MCP servers, with subprocesses and health checks faked (no ports, no network)."""

from __future__ import annotations

import io
import signal
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

from esmeralda import cli, local_mcp

REPO_ROOT = Path(__file__).resolve().parents[3]


class FakeProc:
    """A Popen stand-in: running until killed, or exited with ``exit_code``."""

    _next_pid = 4000

    def __init__(self, cmd, exit_code=None, **kwargs):
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.cmd = cmd
        self.kwargs = kwargs
        self.returncode = exit_code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.returncode


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Fakes Popen, the health check and killpg; servers turn healthy once started."""
    state = {"procs": [], "up": set(), "already_up": set(), "killed": [], "exit_code": None, "never_ready": False}

    def popen(cmd, **kwargs):
        proc = FakeProc(cmd, exit_code=state["exit_code"], **kwargs)
        state["procs"].append(proc)
        if state["exit_code"] is None and not state["never_ready"]:
            state["up"].add(int(cmd[cmd.index("--port") + 1]))
        return proc

    def killpg(pid, sig):
        state["killed"].append((pid, sig))
        for proc in state["procs"]:
            if proc.pid == pid:
                proc.returncode = -sig
                state["up"].discard(int(proc.cmd[proc.cmd.index("--port") + 1]))

    # Replace the modules only as local_mcp sees them, so nothing else in the process is affected.
    fake_subprocess = types.SimpleNamespace(
        Popen=popen, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired
    )
    monkeypatch.setattr(local_mcp, "subprocess", fake_subprocess)
    monkeypatch.setattr(local_mcp, "os", types.SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(local_mcp, "time", types.SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic))
    monkeypatch.setattr(local_mcp, "healthy", lambda port, timeout=1.0: port in state["up"] | state["already_up"])
    return state


def test_server_list_matches_repository():
    """Every server's package directory and uvicorn module exist, ports are unique."""
    assert len({s.port for s in local_mcp.SERVERS}) == len(local_mcp.SERVERS)
    for server in local_mcp.SERVERS:
        module = server.app.split(":", 1)[0]
        assert (REPO_ROOT / server.app_dir / f"{module}.py").is_file(), server
        assert f'name = "{server.package}"' in (REPO_ROOT / server.app_dir / "pyproject.toml").read_text()
        cmd = server.command()
        assert cmd[cmd.index("--host") + 1] == "127.0.0.1"  # never bind beyond loopback


def test_agents_local_env_ports_are_known_servers():
    """The agents' local_env URLs point at servers this module can start."""
    import yaml

    for agent_yaml in sorted((REPO_ROOT / "apps" / "agents").glob("*/agent.yaml")):
        local_env = yaml.safe_load(agent_yaml.read_text()).get("local_env") or {}
        urls = [v for v in local_env.values() if str(v).startswith("http://localhost:")]
        assert len(local_mcp.servers_for(local_env)) == len(urls), agent_yaml


def test_servers_for_picks_local_ports_only():
    env = {
        "EMAIL_MCP_URL": "http://localhost:8001/mcp",
        "DMS_MCP_URL": "http://127.0.0.1:8003/mcp",
        "OTHER": "https://legacy-dms.example.internal/mcp",
        "PORT_ONLY": "8002",
    }
    assert [s.name for s in local_mcp.servers_for(env)] == ["corporate-email", "legacy-dms"]
    assert local_mcp.servers_for({}) == []


def test_find_root(tmp_path):
    (tmp_path / "apps" / "services").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("")
    agent = tmp_path / "apps" / "agents" / "a"
    agent.mkdir(parents=True)
    assert local_mcp.find_root(agent) == tmp_path.resolve()
    with pytest.raises(local_mcp.LocalMcpError):
        local_mcp.find_root(tmp_path.parent)


def test_running_starts_waits_and_stops(fake, tmp_path):
    out = io.StringIO()
    with local_mcp.running(tmp_path, local_mcp.SERVERS, out=out) as started:
        assert [s.name for s in started] == [s.name for s in local_mcp.SERVERS]
        assert fake["up"] == {8001, 8002, 8003}
        assert not fake["killed"]
    assert {sig for _, sig in fake["killed"]} == {signal.SIGTERM}
    assert len(fake["killed"]) == 3 and not fake["up"]
    proc = fake["procs"][0]
    assert proc.kwargs["start_new_session"] is True and proc.kwargs["cwd"] == tmp_path
    assert proc.cmd[:4] == ["uv", "run", "--frozen", "--package"]
    assert (tmp_path / local_mcp.LOG_DIR / "corporate-email.log").is_file()
    assert "Stopping local MCP servers" in out.getvalue()


def test_running_stops_servers_on_error_and_ctrl_c(fake, tmp_path):
    with pytest.raises(RuntimeError), local_mcp.running(tmp_path, out=io.StringIO()):
        raise RuntimeError("query failed")
    assert len(fake["killed"]) == 3

    fake["killed"].clear()
    with pytest.raises(KeyboardInterrupt), local_mcp.running(tmp_path, out=io.StringIO()):
        raise KeyboardInterrupt
    assert len(fake["killed"]) == 3 and not fake["up"]


def test_running_reuses_healthy_servers_and_leaves_them_up(fake, tmp_path):
    fake["already_up"] = {8001, 8002}
    with local_mcp.running(tmp_path, out=io.StringIO()) as started:
        assert [s.name for s in started] == ["legacy-dms"]
    assert len(fake["procs"]) == 1 and len(fake["killed"]) == 1


def test_running_reports_a_server_that_exits(fake, tmp_path):
    fake["exit_code"] = 1
    with pytest.raises(local_mcp.LocalMcpError, match="corporate-email exited with code 1"):
        with local_mcp.running(tmp_path, out=io.StringIO()):
            pytest.fail("the block must not run")


def test_running_times_out_and_stops(fake, tmp_path, monkeypatch):
    fake["never_ready"] = True
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(local_mcp.time, "monotonic", lambda: next(clock))
    with pytest.raises(local_mcp.LocalMcpError, match="not ready after 30s"):
        with local_mcp.running(tmp_path, timeout=30, out=io.StringIO()):
            pytest.fail("the block must not run")
    assert len(fake["killed"]) == 3


def test_stop_escalates_to_sigkill(fake, tmp_path, monkeypatch):
    real_killpg = local_mcp.os.killpg

    def stubborn(pid, sig):  # ignores SIGTERM
        if sig == signal.SIGKILL:
            real_killpg(pid, sig)
        else:
            fake["killed"].append((pid, sig))

    monkeypatch.setattr(local_mcp.os, "killpg", stubborn)
    with local_mcp.running(tmp_path, local_mcp.SERVERS[:1], out=io.StringIO()):
        pass
    assert [sig for _, sig in fake["killed"]] == [signal.SIGTERM, signal.SIGKILL]


# ---- esmeralda query --local-mcp ---------------------------------------------------------------

FAKE_AGENT = """
import os

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

import esmeralda


class FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        text = "email at " + os.environ.get("EMAIL_MCP_URL", "unset")
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


root_agent = Agent(name="fake_agent", model=FakeLlm(model="fake-model"))
app = esmeralda.create_app(root_agent)
"""


@pytest.fixture
def checkout_agent(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "apps" / "services").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    agent = root / "apps" / "agents" / "fake"
    (agent / "agent").mkdir(parents=True)
    (agent / "agent.yaml").write_text(
        textwrap.dedent("""
            name: fake
            framework: google-adk
            env:
              AGENT_NAME: fake_agent
            local_env:
              EMAIL_MCP_URL: "http://localhost:8001/mcp"
              DMS_MCP_URL: "http://localhost:8003/mcp"
        """)
    )
    (agent / "agent" / "__init__.py").write_text(FAKE_AGENT)
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(agent))
    for var in ("EMAIL_MCP_URL", "DMS_MCP_URL", "AGENT_NAME"):
        monkeypatch.delenv(var, raising=False)
    yield agent
    sys.modules.pop("agent", None)


def test_parser_local_mcp_flag():
    parser = cli._parser()
    ns = parser.parse_args(["query", "--agent-dir", "apps/agents/x", "--local-mcp", "hi"])
    assert ns.local_mcp is True and ns.url is None and ns.engine is None
    assert parser.parse_args(["query", "hi"]).local_mcp is False
    for other in (["--url", "http://localhost:8080"], ["--engine", "projects/p/locations/l/reasoningEngines/1"]):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["query", "--local-mcp", *other, "hi"])
        assert exc.value.code == 2  # only for in-process runs


def test_query_local_mcp_starts_the_agents_servers_then_stops_them(fake, checkout_agent, capsys):
    assert cli.main(["query", "--agent-dir", str(checkout_agent), "--local-mcp", "hello"]) == 0
    out = capsys.readouterr()
    assert "email at http://localhost:8001/mcp" in out.out
    ports = sorted(int(p.cmd[p.cmd.index("--port") + 1]) for p in fake["procs"])
    assert ports == [8001, 8003]  # only the servers in local_env
    assert all(p.kwargs["cwd"] == checkout_agent.parents[2] for p in fake["procs"])
    assert len(fake["killed"]) == 2 and not fake["up"]


def test_query_local_mcp_stops_servers_when_interrupted(fake, checkout_agent, monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_query", interrupted)
    assert cli.main(["query", "--agent-dir", str(checkout_agent), "--local-mcp", "hello"]) == 130
    assert len(fake["killed"]) == 2 and not fake["up"]


def test_query_local_mcp_fails_cleanly_when_a_server_does_not_start(fake, checkout_agent, monkeypatch, capsys):
    fake["exit_code"] = 3
    ran = []
    monkeypatch.setattr(cli, "_run_query", lambda *a, **k: ran.append(1) or 0)
    assert cli.main(["query", "--agent-dir", str(checkout_agent), "--local-mcp", "hello"]) == 1
    assert not ran
    assert "exited with code 3" in capsys.readouterr().err
