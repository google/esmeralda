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

"""Guard: the delegated user credential never leaks into telemetry.

A real ADK Runner run (fake model, no network) carries a user token as ``temp:`` state; a tool
builds the MCP headers and A2A metadata exactly as ``mcp.toolset`` and ``a2a.remote_agent`` do.
With a span exporter (plus ``BaggageSpanProcessor``), a log capture at DEBUG, the usage-record
stream, a ``--verbose`` report and stdout/stderr all captured, the token must reach only the
listed targets, and appear nowhere else: not in spans, baggage, logs, usage records, the report,
stdout/stderr or the persisted session.
"""

import asyncio
import io
import json
import logging

import pytest
from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from opentelemetry import baggage, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import esmeralda
from esmeralda import a2a, auth, lifecycle, mcp
from esmeralda.plugin import EsmeraldaTelemetryPlugin
from esmeralda.report import Report
from esmeralda.telemetry import BaggageSpanProcessor, TelemetryEmitter

TOKEN = "synthetic-leak-canary-5d2b9e"  # not a real credential; unique so any copy is found

LISTED_MCP = "https://legacy-dms.esmeralda.internal/mcp"
UNLISTED_MCP = "https://corporate-email.esmeralda.internal/mcp"
LISTED_AGENT = "https://specialist.esmeralda.internal"
UNLISTED_AGENT = "https://other-agent.esmeralda.internal"


class ToolCallingLlm(BaseLlm):
    """Calls the tool once, then answers."""

    async def generate_content_async(self, llm_request, stream=False):
        answered = any(p.function_response for c in llm_request.contents for p in (c.parts or []))
        if answered:
            part = types.Part(text="done")
        else:
            part = types.Part(function_call=types.FunctionCall(name="fetch_documents", args={"applicant": "Sterling"}))
        yield LlmResponse(
            content=types.Content(role="model", parts=[part]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5, total_token_count=15
            ),
            finish_reason=types.FinishReason.STOP,
        )


@pytest.fixture
def exporter():
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(BaggageSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


def _run(monkeypatch, exporter, caplog, capsys):
    monkeypatch.setenv(
        "ESMERALDA_USER_CREDENTIAL_TARGETS",
        json.dumps({"legacy-dms.esmeralda.internal": None, "specialist.esmeralda.internal": None}),
    )
    monkeypatch.setenv("AGENT_NAME", "root")
    monkeypatch.delenv("AGENT_AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("USER_AUTH_ID", raising=False)
    monkeypatch.setattr(auth, "bearer_token", lambda url, kind=None: "service-bearer")

    sent: dict = {}

    def fetch_documents(applicant: str, tool_context: ToolContext) -> dict:
        """Fetches the applicant's documents."""
        # What McpToolset's header_provider and RemoteA2aAgent's meta provider receive.
        readonly = ReadonlyContext(tool_context._invocation_context)
        sent["mcp_listed"] = mcp.headers_for(LISTED_MCP, readonly)
        sent["mcp_unlisted"] = mcp.headers_for(UNLISTED_MCP, readonly)
        ic = tool_context._invocation_context
        sent["a2a_listed"] = a2a.request_metadata_for(LISTED_AGENT)(ic, None)
        sent["a2a_unlisted"] = a2a.request_metadata_for(UNLISTED_AGENT)(ic, None)
        sent["baggage"] = dict(baggage.get_all())
        logging.getLogger("esmeralda.test").info("tool ran for %s", applicant)
        return {"status": "ok", "documents": 2}

    usage = io.StringIO()
    app = esmeralda.create_app(Agent(name="root", model=ToolCallingLlm(model="fake-model"), tools=[fetch_documents]))
    app.plugins[0] = EsmeraldaTelemetryPlugin(
        agent_name="root", finalize_steps=lifecycle.FINALIZE, emitter=TelemetryEmitter(usage)
    )
    report_stream = io.StringIO()
    report = Report(stream=report_stream, color=False, verbose=True)

    async def run():
        sessions = InMemorySessionService()
        runner = Runner(app=app, session_service=sessions)
        session = await sessions.create_session(app_name="agent", user_id="u-1")
        async for event in runner.run_async(
            user_id="u-1",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="fetch Sterling's documents")]),
            state_delta={
                "temp:caller_context": {"project_id": "caller-project", "agent_name": "caller_agent"},
                "temp:user_auth_token": TOKEN,
            },
        ):
            dumped = event.model_dump(mode="json", exclude_none=True)
            report.adk_event(dumped)  # raw, as `esmeralda query --verbose` prints it
            print(json.dumps(dumped))
        stored = await sessions.get_session(app_name="agent", user_id="u-1", session_id=session.id)
        return stored

    with caplog.at_level(logging.DEBUG):
        stored = asyncio.run(run())
    out = capsys.readouterr()

    sinks = {
        "spans": "\n".join(s.to_json() for s in exporter.get_finished_spans()),
        "logs": caplog.text + "\n".join(f"{r.getMessage()} {r.args!r} {r.exc_text or ''}" for r in caplog.records),
        "usage_records": usage.getvalue(),
        "report": report_stream.getvalue(),
        "stdout": out.out,
        "stderr": out.err,
        "persisted_session": stored.model_dump_json(),
        "baggage": json.dumps(sent.get("baggage", {})),
    }
    return sent, sinks, stored


def _leaks(token: str, sinks: dict[str, str]) -> list[str]:
    return [name for name, text in sinks.items() if token in text]


def test_user_credential_reaches_only_listed_targets_and_never_telemetry(monkeypatch, exporter, caplog, capsys):
    sent, sinks, stored = _run(monkeypatch, exporter, caplog, capsys)

    # Forwarded where configured...
    assert sent["mcp_listed"]["X-User-Token"] == TOKEN
    assert sent["a2a_listed"]["user_auth_token"] == TOKEN
    # ...and nowhere else.
    assert TOKEN not in json.dumps(sent["mcp_unlisted"])
    assert TOKEN not in json.dumps(sent["a2a_unlisted"])

    # The run really happened and was observed.
    assert exporter.get_finished_spans(), "no spans captured"
    assert '"genai_token_consumption"' in sinks["usage_records"]
    assert '"tool_execution"' in sinks["usage_records"] or "fetch_documents" in sinks["spans"]
    assert "tool ran for Sterling" in sinks["logs"]
    assert sinks["stdout"] and sinks["report"]
    assert any(s.attributes.get("caller.project_id") == "caller-project" for s in exporter.get_finished_spans())

    assert _leaks(TOKEN, sinks) == [], {k: v[:2000] for k, v in sinks.items() if TOKEN in v}
    assert not [k for k in stored.state if "user_auth_token" in k]


def test_leak_detector_catches_a_planted_leak(monkeypatch, exporter, caplog, capsys):
    """Negative control: the same sinks do show the token when code puts it somewhere it shouldn't."""
    tracer = trace.get_tracer("leak-control")
    with tracer.start_as_current_span("planted") as span:
        span.set_attribute("bad.attribute", TOKEN)
    logging.getLogger("esmeralda.test").warning("planted %s", TOKEN)
    sinks = {
        "spans": "\n".join(s.to_json() for s in exporter.get_finished_spans()),
        "logs": caplog.text,
    }
    assert _leaks(TOKEN, sinks) == ["spans", "logs"]
