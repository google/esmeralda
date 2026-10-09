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

"""esmeralda serve: the ADK command line, the A2A server (queried in memory), and env handling."""

import asyncio
import io
import os
import sys
import textwrap

import httpx
import pytest
from test_query import FAKE_AGENT

from esmeralda import cli, context, serve
from esmeralda import query as q
from esmeralda.config import AgentConfig
from esmeralda.report import Report

A2A_YAML = """
name: fake-a2a
framework: a2a
env:
  AGENT_NAME: fake_agent
local_env:
  FAKE_LOCAL: "on"
agent_card:
  name: fake-a2a
  description: A fake A2A agent
  version: "2.0.0"
  url: https://fake-a2a.esmeralda.internal
  preferred_transport: HTTP+JSON
  capabilities:
    streaming: false
  default_input_modes: ["text/plain"]
  default_output_modes: ["application/json"]
  skills:
    - id: answer
      name: Answer
      description: Answers
      tags: [fake]
"""


@pytest.fixture
def a2a_dir(tmp_path, monkeypatch):
    (tmp_path / "agent.yaml").write_text(textwrap.dedent(A2A_YAML))
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text(FAKE_AGENT)
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(tmp_path))
    for var in ("FAKE_LOCAL", "A2A_AGENT_URL", "GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path
    sys.modules.pop("agent", None)


def test_adk_command():
    config = AgentConfig(directory="/app", name="x", framework="google-adk")  # type: ignore[arg-type]
    assert serve.adk_command(config, host="0.0.0.0", port=8080, otel_to_cloud=True) == [
        "adk", "api_server", "/app", "--host", "0.0.0.0", "--port", "8080", "--no-reload",
        "--gemini_enterprise_app_name", "agent", "--otel_to_cloud",
    ]  # fmt: skip
    assert serve.adk_command(config, host="127.0.0.1", port=9000, web=True)[:2] == ["adk", "web"]


def test_agent_card_from_yaml(a2a_dir, monkeypatch):
    card = serve.agent_card(AgentConfig.load(a2a_dir))
    assert (card.name, card.version, card.url) == ("fake-a2a", "2.0.0", "https://fake-a2a.esmeralda.internal")
    assert [s.id for s in card.skills] == ["answer"] and card.capabilities.streaming is False

    monkeypatch.setenv("A2A_AGENT_URL", "https://override.esmeralda.internal")
    assert serve.agent_card(AgentConfig.load(a2a_dir)).url == "https://override.esmeralda.internal"


def test_a2a_server_answers_on_every_mount(a2a_dir, monkeypatch):
    config = AgentConfig.load(a2a_dir)
    asgi = serve.a2a_server(config)
    monkeypatch.setattr(q, "http_client", lambda **kw: httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), **kw))
    caller = context.CallerContext("caller-project", "caller_agent")

    for mount in ("", "/a2a", "/api/a2a"):
        report = Report(stream=io.StringIO(), color=False)
        asyncio.run(q.run(config, q.Query("hi", caller=caller), report, url=f"http://localhost:8080{mount}"))
        assert report.summary() == 0, report.stream.getvalue()
        assert report.task_state == "completed" and "fake answer" in report.answer
    assert sys.modules["agent"].SEEN_CALLERS[-1] == caller


def test_serve_adk_execs_with_env(tmp_path, monkeypatch):
    (tmp_path / "agent.yaml").write_text(
        "name: x\nframework: google-adk\nenv:\n  FAKE_ENV: deployed\nlocal_env:\n  FAKE_LOCAL: 'on'\n"
    )
    for var in ("FAKE_ENV", "FAKE_LOCAL", "PORT"):
        monkeypatch.delenv(var, raising=False)
    calls = []
    monkeypatch.setattr(serve.os, "chdir", lambda path: calls.append(("chdir", str(path))))
    monkeypatch.setattr(serve.os, "execvp", lambda file, args: calls.append(("exec", args)))

    serve.serve(AgentConfig.load(tmp_path), otel_to_cloud=True)
    assert os.environ["FAKE_ENV"] == "deployed" and "FAKE_LOCAL" not in os.environ  # local_env only with local=True
    assert calls[-1][1][:3] == ["adk", "api_server", str(tmp_path)] and "--otel_to_cloud" in calls[-1][1]
    assert "8080" in calls[-1][1]

    serve.serve(AgentConfig.load(tmp_path), local=True, port=9001)
    assert os.environ["FAKE_LOCAL"] == "on" and "9001" in calls[-1][1]


def test_serve_web_is_adk_only(a2a_dir):
    with pytest.raises(ValueError, match="ADK"):
        serve.serve(AgentConfig.load(a2a_dir), web=True)


def test_cli_serve_without_agent_yaml(tmp_path, capsys):
    assert cli.main(["serve", "--agent-dir", str(tmp_path)]) == 2
    assert "agent.yaml" in capsys.readouterr().err
