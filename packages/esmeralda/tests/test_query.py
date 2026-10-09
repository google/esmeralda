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

"""esmeralda query: config, report, and the three targets against real ADK/A2A stacks (no network)."""

import asyncio
import io
import json
import sys
import textwrap

import httpx
import pytest

from esmeralda import cli, context
from esmeralda import query as q
from esmeralda.config import AgentConfig
from esmeralda.report import Report

FAKE_AGENT = """
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

import esmeralda
from esmeralda import context

SEEN_CALLERS = []


class FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="fake answer")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5, total_token_count=15
            ),
        )


class RecordCaller(BasePlugin):
    def __init__(self):
        super().__init__(name="record_caller")

    async def before_run_callback(self, *, invocation_context):
        SEEN_CALLERS.append(context.from_invocation(invocation_context))


root_agent = Agent(name="fake_agent", model=FakeLlm(model="fake-model"))
app = esmeralda.create_app(root_agent, plugins=[RecordCaller()])
"""


@pytest.fixture
def agent_dir(tmp_path, monkeypatch):
    (tmp_path / "agent.yaml").write_text(
        textwrap.dedent("""
            name: fake-agent
            framework: google-adk
            env:
              AGENT_NAME: fake_agent
              FAKE_FLAG: true
            local_env:
              FAKE_LOCAL: "on"
        """)
    )
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text(FAKE_AGENT)
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("FAKE_LOCAL", raising=False)
    monkeypatch.delenv("FAKE_FLAG", raising=False)
    yield tmp_path
    sys.modules.pop("agent", None)


def report():
    return Report(stream=io.StringIO(), color=False)


# ---- config ----------------------------------------------------------------------------------


def test_config_load_and_local_env(agent_dir, monkeypatch):
    config = AgentConfig.load(agent_dir)
    assert (config.name, config.framework, config.is_a2a) == ("fake-agent", "google-adk", False)
    assert config.env["FAKE_FLAG"] == "true"

    monkeypatch.setenv("AGENT_NAME", "from_shell")
    environ = {"AGENT_NAME": "from_shell"}
    config.apply_local_env(environ)
    assert environ == {"AGENT_NAME": "from_shell", "FAKE_FLAG": "true", "FAKE_LOCAL": "on"}


def test_config_rejects_unknown_framework(tmp_path):
    (tmp_path / "agent.yaml").write_text("framework: langchain\n")
    with pytest.raises(ValueError, match="framework"):
        AgentConfig.load(tmp_path)


def test_config_requires_agent_yaml(tmp_path):
    with pytest.raises(FileNotFoundError):
        AgentConfig.load(tmp_path)


# ---- helpers ---------------------------------------------------------------------------------


def test_parse_caller():
    assert q.parse_caller("p/a") == context.CallerContext("p", "a")
    with pytest.raises(ValueError):
        q.parse_caller("/")


def test_engine_base_url():
    engine = "projects/p/locations/us-central1/reasoningEngines/123"
    assert q.engine_base_url(engine) == f"https://us-central1-aiplatform.googleapis.com/v1beta1/{engine}"
    with pytest.raises(ValueError):
        q.engine_base_url("reasoningEngines/123")


# ---- report ----------------------------------------------------------------------------------


def test_report_adk_events_and_verdict():
    r = report()
    r.adk_event(
        {
            "author": "root",
            "content": {"parts": [{"function_call": {"name": "transfer_to_agent", "args": {"agent_name": "tools"}}}]},
        }
    )
    r.adk_event({"author": "tools", "content": {"parts": [{"function_call": {"name": "search", "args": {}}}]}})
    r.adk_event(
        {
            "author": "tools",
            "content": {"parts": [{"function_response": {"name": "search", "response": {"isError": True}}}]},
        }
    )
    r.adk_event({"author": "tools", "partial": True, "content": {"parts": [{"text": "par"}]}})
    r.adk_event(
        {
            "author": "tools",
            "content": {"parts": [{"text": "full answer"}]},
            "usage_metadata": {"prompt_token_count": 3, "candidates_token_count": 2, "total_token_count": 5},
        }
    )
    assert (r.answer_author, r.answer) == ("tools", "full answer")
    assert (r.tool_calls, r.tool_errors, r.authors) == (2, 1, ["root", "tools"])
    assert r.tokens == {"prompt": 3, "output": 2, "total": 5}
    assert r.summary() == 0
    assert r.summary(fail_on_tool_error=True) == 1


def test_report_fails_on_error_event_or_no_answer():
    r = report()
    r.adk_event({"author": "root", "error_code": "MODEL_ERROR"})
    assert r.summary() == 1
    assert report().summary() == 1  # no answer at all


def test_report_a2a_task():
    task = {
        "status": {"state": "completed"},
        "history": [
            {
                "parts": [
                    {
                        "kind": "data",
                        "data": {"name": "lookup", "args": {"id": 1}},
                        "metadata": {"adk_type": "function_call"},
                    }
                ]
            },
            {
                "parts": [
                    {
                        "kind": "data",
                        "data": {"name": "lookup", "response": {}},
                        "metadata": {"adk_type": "function_response"},
                    }
                ]
            },
        ],
        "artifacts": [{"parts": [{"kind": "text", "text": "the answer"}]}],
        "metadata": {"adk_usage_metadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10}},
    }
    r = report()
    r.a2a_task(task)
    assert (r.task_state, r.answer, r.tool_calls, r.tokens["total"]) == ("completed", "the answer", 1, 10)
    assert r.summary() == 0

    failed = report()
    failed.a2a_task({"status": {"state": "failed", "message": {"parts": [{"text": "boom"}]}}})
    assert failed.summary() == 1
    assert "boom" in failed.stream.getvalue()


# ---- targets ---------------------------------------------------------------------------------


def test_in_process_target(agent_dir):
    config = AgentConfig.load(agent_dir)
    r = report()
    caller = context.CallerContext("caller-project", "caller_agent")
    asyncio.run(q.run(config, q.Query("hi", caller=caller), r))

    assert r.answer == "fake answer" and r.summary() == 0
    assert sys.modules["agent"].SEEN_CALLERS == [caller]


def test_adk_url_target_sends_caller_as_state(monkeypatch, tmp_path):
    (tmp_path / "agent.yaml").write_text("name: x\nframework: google-adk\n")
    received = {}

    async def server(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["body"] = json.loads(request.content)
        lines = [
            json.dumps({"author": "root", "content": {"parts": [{"text": "streamed answer"}]}}),
            "",
        ]
        return httpx.Response(200, text="\n".join(lines))

    monkeypatch.setattr(q, "http_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(server), **kw))
    r = report()
    asyncio.run(
        q.run(
            AgentConfig.load(tmp_path),
            q.Query("hello", caller=context.CallerContext("p", "a")),
            r,
            url="http://localhost:8080/",
        )
    )

    assert received["url"] == "http://localhost:8080/api/stream_reasoning_engine"
    assert received["body"]["class_method"] == "async_stream_query"
    assert received["body"]["input"]["state_delta"] == {"temp:caller_context": {"project_id": "p", "agent_name": "a"}}
    assert r.answer == "streamed answer"


def test_a2a_url_target_against_a_real_a2a_server(agent_dir, monkeypatch):
    """A real A2A REST server (ADK executor + fake model): card, message:send, caller metadata."""
    from a2a.server.apps import A2ARESTFastAPIApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCapabilities, AgentCard
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    config = AgentConfig.load(agent_dir)
    app = q.load_app(config)
    executor = A2aAgentExecutor(runner=Runner(app=app, session_service=InMemorySessionService()))
    card = AgentCard(
        name="fake-agent",
        description="fake",
        url="https://fake-agent.esmeralda.internal",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
        preferred_transport="HTTP+JSON",
    )
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    asgi = A2ARESTFastAPIApplication(agent_card=card, http_handler=handler).build()
    monkeypatch.setattr(q, "http_client", lambda **kw: httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), **kw))

    a2a_config = AgentConfig(directory=agent_dir, name="fake-agent", framework="a2a")
    caller = context.CallerContext("caller-project", "caller_agent")
    r = report()
    asyncio.run(q.run(a2a_config, q.Query("hi", caller=caller), r, url="http://localhost:8080"))

    assert r.summary() == 0, r.stream.getvalue()
    assert r.task_state == "completed" and "fake answer" in r.answer
    assert sys.modules["agent"].SEEN_CALLERS[-1] == caller


# ---- CLI -------------------------------------------------------------------------------------


def test_cli_query_exit_codes(agent_dir, capsys):
    assert cli.main(["query", "--agent-dir", str(agent_dir), "hi"]) == 0
    assert "PASSED" in capsys.readouterr().out
    assert cli.main(["query", "--agent-dir", str(agent_dir / "missing"), "hi"]) == 2
    assert cli.main(["query", "--url", "http://a", "--engine", "projects/p/locations/l/reasoningEngines/1", "hi"]) == 2
