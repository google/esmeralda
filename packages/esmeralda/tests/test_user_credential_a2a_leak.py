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

"""Guard: over A2A, the user credential reaches the callee's run but never its stored session.

ADK's default A2A request converter puts the request metadata (``user_auth_token`` included) in
``run_config.custom_metadata``, and the runner copies that into every event's ``custom_metadata``,
which session services persist. ``esmeralda serve`` uses ``esmeralda.a2a.request_converter``,
which moves the token into ``temp:`` state instead. These tests drive the real A2A server
(``serve.a2a_server``) and a real ``RemoteA2aAgent`` caller, then search every stored session.
"""

import asyncio
import io
import json
import sys
import textwrap

import httpx
import pytest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from esmeralda import a2a, context, serve
from esmeralda import query as q
from esmeralda.config import AgentConfig
from esmeralda.report import Report

TOKEN = "synthetic-a2a-canary-91f04c"  # not a real credential
BASE = "http://localhost:8080"

CALLEE_YAML = """
name: callee
framework: a2a
env:
  AGENT_NAME: callee_agent
agent_card:
  name: callee
  description: callee
  version: "1.0.0"
  url: http://localhost:8080
  preferred_transport: HTTP+JSON
  capabilities:
    streaming: false
"""

CALLEE_AGENT = """
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

import esmeralda
from esmeralda import context

SEEN = []


class FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="callee answer")]))


class Record(BasePlugin):
    def __init__(self):
        super().__init__(name="record")

    async def before_run_callback(self, *, invocation_context):
        SEEN.append((context.user_token(invocation_context), context.from_invocation(invocation_context)))


root_agent = Agent(name="callee_agent", model=FakeLlm(model="fake-model"))
app = esmeralda.create_app(root_agent, plugins=[Record()])
"""


@pytest.fixture
def callee(tmp_path, monkeypatch):
    (tmp_path / "agent.yaml").write_text(textwrap.dedent(CALLEE_YAML))
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text(CALLEE_AGENT)
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(tmp_path))
    for var in ("A2A_AGENT_URL", "GOOGLE_CLOUD_AGENT_ENGINE_ID", "USER_AUTH_ID", "AGENT_AUTH_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    sessions = InMemorySessionService()
    monkeypatch.setattr(serve, "session_service", lambda: sessions)
    config = AgentConfig.load(tmp_path)
    yield config, sessions
    sys.modules.pop("agent", None)


def _stored(service: InMemorySessionService) -> str:
    """Every stored session (state and events) of an in-memory session service, as JSON."""
    dumps = [
        session.model_dump_json()
        for users in service.sessions.values()
        for by_id in users.values()
        for session in by_id.values()
    ]
    assert dumps, "no session stored"
    return "\n".join(dumps)


def _query(config, asgi, monkeypatch, *, token=TOKEN):
    monkeypatch.setattr(q, "http_client", lambda **kw: httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), **kw))
    report = Report(stream=io.StringIO(), color=False)
    caller = context.CallerContext("caller-project", "caller_agent")
    asyncio.run(q.run(config, q.Query("hi", caller=caller, user_token=token), report, url=BASE))
    assert report.summary() == 0, report.stream.getvalue()
    return report


def test_a2a_callee_gets_the_token_but_never_stores_it(callee, monkeypatch):
    config, sessions = callee
    _query(config, serve.a2a_server(config), monkeypatch)

    token, caller = sys.modules["agent"].SEEN[-1]
    assert token == TOKEN  # the run can still use (and forward) it...
    assert caller == context.CallerContext("caller-project", "caller_agent")  # ...other metadata intact
    stored = _stored(sessions)
    assert TOKEN not in stored  # ...but no stored event or state holds it
    assert "caller_context" in stored  # the rest of the A2A metadata is still recorded


def test_default_adk_converter_would_store_the_token(callee, monkeypatch):
    """Negative control: with ADK's default converter the same run persists the token."""
    from google.adk.a2a.converters.request_converter import convert_a2a_request_to_agent_run_request

    config, sessions = callee
    monkeypatch.setattr(a2a, "request_converter", convert_a2a_request_to_agent_run_request)
    _query(config, serve.a2a_server(config), monkeypatch)
    assert TOKEN in _stored(sessions)


def test_a2a_caller_forwards_the_token_without_storing_it(callee, monkeypatch):
    """Orchestrator side: a RemoteA2aAgent caller forwards the token and keeps it out of its own events."""
    config, callee_sessions = callee
    asgi = serve.a2a_server(config)
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", json.dumps({"localhost:8080": None}))
    monkeypatch.setenv("AGENT_NAME", "caller_agent")
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=asgi)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    remote = a2a.remote_agent("callee", url=BASE)
    caller_sessions = InMemorySessionService()

    async def run():
        runner = Runner(app_name="caller", agent=remote, session_service=caller_sessions)
        session = await caller_sessions.create_session(app_name="caller", user_id="u-1")
        async for _ in runner.run_async(
            user_id="u-1",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="ask the callee")]),
            state_delta={"temp:user_auth_token": TOKEN},
        ):
            pass

    asyncio.run(run())

    token, _ = sys.modules["agent"].SEEN[-1]
    assert token == TOKEN  # forwarded to the listed callee
    caller_stored = _stored(caller_sessions)
    assert "a2a:request" in caller_stored or "a2a:response" in caller_stored  # the A2A exchange is recorded
    assert TOKEN not in caller_stored  # but not the credential
    assert TOKEN not in _stored(callee_sessions)
