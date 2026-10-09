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

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import esmeralda
from esmeralda import context
from esmeralda.plugin import EsmeraldaTelemetryPlugin
from esmeralda.telemetry import BaggageSpanProcessor, TelemetryEmitter


def invocation(invocation_id, state=None, a2a_metadata=None):
    run_config = SimpleNamespace(custom_metadata={"a2a_metadata": a2a_metadata}) if a2a_metadata else None
    return SimpleNamespace(
        invocation_id=invocation_id, session=SimpleNamespace(id="s-1", state=state or {}), run_config=run_config
    )


@pytest.fixture
def tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BaggageSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


@pytest.fixture
def events():
    stream = io.StringIO()
    plugin = EsmeraldaTelemetryPlugin(agent_name="test_agent", finalize_steps=(), emitter=TelemetryEmitter(stream))

    def read():
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    return plugin, read


# ---- caller context --------------------------------------------------------------------------


def test_caller_from_session_state():
    ic = invocation("i", state={context.STATE_KEY: {"project_id": "p", "agent_name": "a"}})
    assert context.from_invocation(ic) == context.CallerContext("p", "a")


def test_caller_from_a2a_metadata():
    ic = invocation("i", a2a_metadata={"caller_context": {"project_id": "p", "agent_name": "a"}})
    assert context.from_invocation(ic) == context.CallerContext("p", "a")


@pytest.mark.parametrize("value", [None, "text", {}, {"project_id": 3}, {"project_id": " "}])
def test_unusable_caller_is_ignored(value):
    assert context.from_invocation(invocation("i", state={context.STATE_KEY: value})) is None


def test_caller_values_are_bounded():
    caller = context.CallerContext.from_mapping({"project_id": "x" * 500})
    assert caller == context.CallerContext("x" * 128, "unknown")


def test_outgoing_metadata_identifies_this_agent(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cx-project")
    monkeypatch.setenv("AGENT_NAME", "cx_mortgage_orchestrator")
    assert context.outgoing_metadata() == {
        "caller_context": {"project_id": "cx-project", "agent_name": "cx_mortgage_orchestrator"}
    }


# ---- baggage on spans ------------------------------------------------------------------------


def test_spans_carry_caller_only_during_the_run(tracing, events):
    tracer, exporter = tracing
    plugin, _ = events
    ic = invocation("i-1", state={context.STATE_KEY: {"project_id": "p", "agent_name": "a"}})

    async def run():
        await plugin.before_run_callback(invocation_context=ic)
        with tracer.start_as_current_span("inside"):
            pass
        await plugin.after_run_callback(invocation_context=ic)
        with tracer.start_as_current_span("after"):
            pass

    asyncio.run(run())
    spans = {s.name: dict(s.attributes) for s in exporter.get_finished_spans()}
    assert spans["inside"] == {"caller.project_id": "p", "caller.agent_name": "a"}
    assert spans["after"] == {}


def test_concurrent_runs_do_not_share_baggage(tracing, events):
    tracer, exporter = tracing
    plugin, _ = events

    async def run(name):
        ic = invocation(name, state={context.STATE_KEY: {"project_id": name, "agent_name": name}})
        await plugin.before_run_callback(invocation_context=ic)
        for _ in range(3):
            await asyncio.sleep(0)
            with tracer.start_as_current_span(name):
                pass
        await plugin.after_run_callback(invocation_context=ic)

    async def main():
        await asyncio.gather(run("one"), run("two"))

    asyncio.run(main())
    for span in exporter.get_finished_spans():
        assert span.attributes["caller.project_id"] == span.name


def test_before_run_finalizes():
    calls = []
    plugin = EsmeraldaTelemetryPlugin(finalize_steps=(lambda: calls.append("finalize"),))
    asyncio.run(plugin.before_run_callback(invocation_context=invocation("i")))
    assert calls == ["finalize"]


# ---- telemetry events ------------------------------------------------------------------------


def tool_context(call_id="call-1"):
    return SimpleNamespace(function_call_id=call_id, session=SimpleNamespace(id="s-1"), user_id="u-1")


def test_tool_success_event(events):
    plugin, read = events
    tool, ctx = SimpleNamespace(name="search_documents"), tool_context()

    async def run():
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=ctx)
        assert await plugin.after_tool_callback(tool=tool, tool_args={}, tool_context=ctx, result={"ok": 1}) is None

    asyncio.run(run())
    (event,) = read()
    assert event["event"] == "mcp_tool_execution"
    assert (event["tool_name"], event["status"], event["session_id"], event["user_id"]) == (
        "search_documents",
        "SUCCESS",
        "s-1",
        "u-1",
    )
    assert event["duration_ms"] >= 0


def test_tool_error_event(events):
    plugin, read = events
    tool, ctx = SimpleNamespace(name="read_inbox"), tool_context()
    result = asyncio.run(
        plugin.on_tool_error_callback(tool=tool, tool_args={}, tool_context=ctx, error=TimeoutError("slow"))
    )
    assert result is None
    (event,) = read()
    assert (event["status"], event["error_reason"]) == ("ERROR", "TimeoutError: slow")


def test_model_token_event(events):
    plugin, read = events
    usage = SimpleNamespace(
        prompt_token_count=100,
        candidates_token_count=50,
        total_token_count=170,
        thoughts_token_count=20,
        cached_content_token_count=40,
    )
    response = SimpleNamespace(
        usage_metadata=usage, partial=False, model_version="gemini-x", finish_reason=SimpleNamespace(name="STOP")
    )
    ctx = SimpleNamespace(session=SimpleNamespace(id="s-1"), user_id="u-1")
    asyncio.run(plugin.after_model_callback(callback_context=ctx, llm_response=response))

    (event,) = read()
    assert event["event"] == "genai_token_consumption"
    assert event["agent_id"] == "test_agent" and event["model"] == "gemini-x"
    assert event["tokens"] == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "thoughts_tokens": 20,
        "cached_tokens": 40,
        "total_tokens": 170,
    }
    assert event["implicit_caching"] == {"cache_hit": True, "cache_hit_ratio": 0.4}


def test_partial_model_responses_are_skipped(events):
    plugin, read = events
    response = SimpleNamespace(usage_metadata=SimpleNamespace(), partial=True)
    asyncio.run(plugin.after_model_callback(callback_context=SimpleNamespace(), llm_response=response))
    assert read() == []


def test_telemetry_errors_do_not_break_the_run(events, caplog):
    plugin, _ = events
    response = SimpleNamespace(usage_metadata=object(), partial=False)  # no token fields
    asyncio.run(plugin.after_model_callback(callback_context=SimpleNamespace(), llm_response=response))
    assert "Failed to emit model telemetry" in caplog.text


# ---- app ------------------------------------------------------------------------------------


def test_create_app_puts_esmeralda_plugin_first():
    from google.adk.agents import Agent

    extra = EsmeraldaTelemetryPlugin(agent_name="other")
    extra.name = "extra"
    app = esmeralda.create_app(Agent(name="root", model="gemini-x"), plugins=[extra])
    assert app.name == "agent"
    assert [p.name for p in app.plugins] == ["esmeralda_telemetry", "extra"]
