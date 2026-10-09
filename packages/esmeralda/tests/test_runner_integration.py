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

"""End to end through a real ADK Runner (fake model, no network).

Checks what the design relies on: the caller context sent as ``state_delta`` is visible to the
plugin's before_run_callback, the baggage reaches the spans ADK creates, and token telemetry is
emitted from the real callback arguments.
"""

import asyncio
import io
import json

import pytest
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import esmeralda
from esmeralda import lifecycle
from esmeralda.plugin import EsmeraldaTelemetryPlugin
from esmeralda.telemetry import TelemetryEmitter


class FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="hello")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5, total_token_count=15
            ),
            finish_reason=types.FinishReason.STOP,
        )


@pytest.fixture(scope="module")
def exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


def test_runner_carries_caller_context_and_emits_tokens(exporter):
    stream = io.StringIO()
    app = esmeralda.create_app(Agent(name="root", model=FakeLlm(model="fake-model")))
    app.plugins[0] = EsmeraldaTelemetryPlugin(
        agent_name="root", finalize_steps=lifecycle.FINALIZE, emitter=TelemetryEmitter(stream)
    )

    async def run():
        sessions = InMemorySessionService()
        runner = Runner(app=app, session_service=sessions)
        session = await sessions.create_session(app_name="agent", user_id="u-1")
        events = []
        async for event in runner.run_async(
            user_id="u-1",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
            state_delta={"temp:caller_context": {"project_id": "caller-project", "agent_name": "caller_agent"}},
        ):
            events.append(event)
        stored = await sessions.get_session(app_name="agent", user_id="u-1", session_id=session.id)
        return events, stored

    events, stored = asyncio.run(run())

    assert any(e.content and e.content.parts and e.content.parts[0].text == "hello" for e in events)
    assert "temp:caller_context" not in stored.state  # temp: state is never persisted

    tagged = [s for s in exporter.get_finished_spans() if s.attributes.get("caller.project_id") == "caller-project"]
    assert tagged, [s.name for s in exporter.get_finished_spans()]
    assert all(s.attributes.get("caller.agent_name") == "caller_agent" for s in tagged)

    (token_event,) = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert token_event["event"] == "genai_token_consumption"
    assert token_event["tokens"]["total_tokens"] == 15
    assert token_event["user_id"] == "u-1" and token_event["session_id"] == stored.id
