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
from esmeralda import context, usage
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
def stream(monkeypatch):
    for var in ("ESMERALDA_WORKLOAD_NAME", "ESMERALDA_TEAM", "ESMERALDA_RUNTIME", "K_SERVICE", "USER_AUTH_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ESMERALDA_LEGACY_TELEMETRY_EVENTS", raising=False)
    return io.StringIO()


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.fixture
def events(stream):
    """The plugin, and a reader of the legacy events it wrote."""
    plugin = EsmeraldaTelemetryPlugin(agent_name="test_agent", finalize_steps=(), emitter=TelemetryEmitter(stream))

    def read():
        return [e for e in _lines(stream) if "schema" not in e]

    return plugin, read


@pytest.fixture
def records(stream):
    """A reader of the esmeralda.usage records the plugin wrote (same stream as ``events``)."""

    def read():
        return [e for e in _lines(stream) if e.get("schema") == "esmeralda.usage"]

    return read


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


def test_partial_model_responses_are_skipped(events, records):
    plugin, read = events
    response = SimpleNamespace(usage_metadata=SimpleNamespace(), partial=True)
    asyncio.run(plugin.after_model_callback(callback_context=SimpleNamespace(), llm_response=response))
    assert read() == [] and records() == []


def test_telemetry_errors_do_not_break_the_run(events, caplog):
    plugin, _ = events
    response = SimpleNamespace(usage_metadata=object(), partial=False)  # no token fields
    asyncio.run(plugin.after_model_callback(callback_context=SimpleNamespace(), llm_response=response))
    assert "Failed to emit model telemetry" in caplog.text


# ---- esmeralda.usage records -----------------------------------------------------------------


SECRET = "ya29" + ".delegated-user-credential-value"  # split so secret scanners stay quiet


def adk_invocation(invocation_id="inv-1", caller=True):
    state = {"temp:user_auth_token": SECRET}
    if caller:
        state[context.STATE_KEY] = {"project_id": "example-cx-project", "agent_name": "cx_mortgage_orchestrator"}
    session = SimpleNamespace(id="s-1", state=state)
    run_config = SimpleNamespace(custom_metadata={"a2a_metadata": {"user_auth_token": SECRET}})
    return SimpleNamespace(
        invocation_id=invocation_id, session=session, run_config=run_config, agent=SimpleNamespace(name="root_agent")
    )


def callback_ctx(ic, agent_name="root_agent", call_id=None):
    return SimpleNamespace(
        invocation_id=ic.invocation_id,
        agent_name=agent_name,
        session=ic.session,
        user_id="u-1",
        state=ic.session.state,
        function_call_id=call_id,
    )


def model_response(**overrides):
    usage_metadata = SimpleNamespace(
        prompt_token_count=100,
        candidates_token_count=50,
        total_token_count=170,
        thoughts_token_count=20,
        cached_content_token_count=40,
        tool_use_prompt_token_count=5,
    )
    for key, value in overrides.items():
        setattr(usage_metadata, key, value)
    return SimpleNamespace(
        usage_metadata=usage_metadata,
        partial=False,
        model_version="gemini-x-001",
        finish_reason=SimpleNamespace(name="STOP"),
    )


def run_invocation(plugin, ic, *, tool_error=None, model_error=None):
    async def run():
        await plugin.before_run_callback(invocation_context=ic)
        ctx = callback_ctx(ic)
        request = SimpleNamespace(model="gemini-x")
        await plugin.before_model_callback(callback_context=ctx, llm_request=request)
        if model_error:
            await plugin.on_model_error_callback(callback_context=ctx, llm_request=request, error=model_error)
        else:
            await plugin.after_model_callback(callback_context=ctx, llm_response=model_response())
        tool = SimpleNamespace(name="verify_income")
        tctx = callback_ctx(ic, call_id="call-1")
        await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=tctx)
        if tool_error:
            await plugin.on_tool_error_callback(tool=tool, tool_args={}, tool_context=tctx, error=tool_error)
        else:
            await plugin.after_tool_callback(tool=tool, tool_args={}, tool_context=tctx, result={"ok": 1})
        await plugin.before_model_callback(callback_context=ctx, llm_request=request)
        await plugin.after_model_callback(callback_context=ctx, llm_response=model_response(thoughts_token_count=None))
        await plugin.after_run_callback(invocation_context=ic)

    asyncio.run(run())


def test_invocation_produces_schema_valid_records(events, records, assert_valid_usage, monkeypatch):
    monkeypatch.setenv("ESMERALDA_TEAM", "ai-coe")
    monkeypatch.setenv("K_SERVICE", "loan-agent")
    plugin, _ = events
    run_invocation(plugin, adk_invocation())

    produced = records()
    assert [r["event"] for r in produced] == ["llm.call", "tool.call", "llm.call", "agent.invocation"]
    for record in produced:
        assert_valid_usage(record)
        assert record["workload"] == {
            "name": "test_agent",
            "team": "ai-coe",
            "runtime": "cloud_run",
            "framework": "adk",
            "cloud": "gcp",
        }
        assert record["caller"] == {
            "verified": False,
            "declared": {"project_id": "example-cx-project", "agent_name": "cx_mortgage_orchestrator"},
        }
        assert record["source"] == "workload" and record["status"] == "ok"

    llm, tool, llm2, agent = produced
    # OTel GenAI semantics: input = prompt + tool-use prompt; output = candidates + reasoning.
    assert llm["gen_ai"] == {
        "system": "gcp.vertex_ai",
        "operation.name": "generate_content",
        "request.model": "gemini-x",
        "response.model": "gemini-x-001",
        "response.finish_reasons": ["stop"],
        "usage.input_tokens": 105,
        "usage.output_tokens": 70,
        "usage.cached_tokens": 40,
        "usage.reasoning_tokens": 20,
        "agent.name": "root_agent",
        "conversation.id": "s-1",
    }
    assert llm["latency_ms"] >= 0
    assert llm2["gen_ai"]["usage.output_tokens"] == 50 and llm2["gen_ai"]["usage.reasoning_tokens"] == 0
    assert tool["tool"]["name"] == "verify_income" and tool["tool"]["status"] == "ok"
    assert tool["tool"]["call_id"] == "call-1" and tool["tool"]["duration_ms"] >= 0
    assert agent["gen_ai"] == {
        "operation.name": "invoke_agent",
        "agent.name": "root_agent",
        "conversation.id": "s-1",
        "usage.input_tokens": 210,
        "usage.output_tokens": 120,
        "usage.cached_tokens": 80,
        "usage.reasoning_tokens": 20,
    }
    assert agent["latency_ms"] >= 0


def test_errors_are_recorded_by_type_only(events, records, assert_valid_usage):
    plugin, read = events
    run_invocation(
        plugin,
        adk_invocation(),
        tool_error=PermissionError(f"Bearer {SECRET} rejected"),
        model_error=RuntimeError("quota"),
    )
    produced = records()
    for record in produced:
        assert_valid_usage(record)
    llm_error = produced[0]
    assert llm_error["status"] == "error" and llm_error["error_type"] == "RuntimeError"
    assert llm_error["gen_ai"]["request.model"] == "gemini-x"
    tool = next(r for r in produced if r["event"] == "tool.call")
    assert tool["tool"] == {
        "name": "verify_income",
        "call_id": "call-1",
        "status": "error",
        "error_type": "PermissionError",
        "duration_ms": tool["tool"]["duration_ms"],
    }
    agent = produced[-1]
    assert agent["status"] == "error" and agent["error_type"] == "RuntimeError"
    # The legacy event keeps the message, redacted.
    (legacy_tool,) = [e for e in read() if e["event"] == "mcp_tool_execution"]
    assert legacy_tool["error_reason"] == "PermissionError: [REDACTED] rejected"


def test_user_credential_never_leaves_the_process(events, stream):
    plugin, _ = events
    run_invocation(plugin, adk_invocation(), tool_error=ValueError(f"token={SECRET}"))
    assert "delegated-user-credential" not in stream.getvalue()


def test_runs_without_caller_context_have_no_caller(events, records, assert_valid_usage):
    plugin, _ = events
    run_invocation(plugin, adk_invocation(caller=False))
    for record in records():
        assert "caller" not in record
        assert_valid_usage(record)


def test_legacy_events_can_be_turned_off(stream, records, monkeypatch):
    monkeypatch.setenv("ESMERALDA_LEGACY_TELEMETRY_EVENTS", "false")
    plugin = EsmeraldaTelemetryPlugin(agent_name="a", finalize_steps=(), emitter=TelemetryEmitter(stream))
    run_invocation(plugin, adk_invocation())
    assert all(e.get("schema") == "esmeralda.usage" for e in _lines(stream))
    assert len(records()) == 4


def test_custom_recorder(events):
    capture = []

    class Transport:
        def emit(self, record):
            capture.append(record)

    recorder = usage.UsageRecorder(Transport(), workload=usage.Workload("custom"))
    plugin = EsmeraldaTelemetryPlugin(finalize_steps=(), recorder=recorder, legacy_events=False)
    run_invocation(plugin, adk_invocation())
    assert {r["workload"]["name"] for r in capture} == {"custom"}


def test_tool_server_from_mcp_connection():
    from esmeralda.plugin import _tool_server

    mcp_tool = SimpleNamespace(
        _mcp_session_manager=SimpleNamespace(
            _connection_params=SimpleNamespace(url="https://income-verification.esmeralda.internal/mcp")
        )
    )
    assert _tool_server(mcp_tool) == "income-verification"
    assert _tool_server(SimpleNamespace(name="fn")) is None


# ---- app ------------------------------------------------------------------------------------


def test_create_app_puts_esmeralda_plugin_first():
    from google.adk.agents import Agent

    extra = EsmeraldaTelemetryPlugin(agent_name="other")
    extra.name = "extra"
    app = esmeralda.create_app(Agent(name="root", model="gemini-x"), plugins=[extra])
    assert app.name == "agent"
    assert [p.name for p in app.plugins] == ["esmeralda_telemetry", "extra"]
