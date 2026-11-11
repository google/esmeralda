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

import io
import json
from datetime import datetime, timezone

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode

from esmeralda import context, usage
from esmeralda.telemetry import BaggageSpanProcessor, TelemetryEmitter

WORKLOAD_ENV = (
    "ESMERALDA_WORKLOAD_NAME",
    "AGENT_NAME",
    "OTEL_SERVICE_NAME",
    "K_SERVICE",
    "ESMERALDA_TEAM",
    "ESMERALDA_RUNTIME",
    "ESMERALDA_FRAMEWORK",
    "ESMERALDA_CLOUD",
    "GOOGLE_CLOUD_AGENT_ENGINE_ID",
    "KUBERNETES_SERVICE_HOST",
    "USER_AUTH_ID",
    usage.TRANSPORT_ENV,
    usage.VALIDATE_ENV,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in WORKLOAD_ENV:
        monkeypatch.delenv(var, raising=False)
    yield
    usage.set_recorder(None)


class Capture:
    def __init__(self):
        self.records = []

    def emit(self, record):
        self.records.append(json.loads(json.dumps(record)))


@pytest.fixture
def capture():
    return Capture()


@pytest.fixture
def recorder(capture):
    return usage.UsageRecorder(capture, workload=usage.Workload("loan-agent", runtime="cloud_run", framework="custom"))


# ---- builders --------------------------------------------------------------------------------


def test_llm_call_record(recorder, capture, assert_valid_usage):
    caller = usage.Caller.from_declared({"project_id": "example-cx-project", "agent_name": "cx"})
    record = recorder.record_llm_call(
        request_model="gemini-flash",
        response_model="gemini-flash-001",
        system="gcp.vertex_ai",
        input_tokens=120,
        output_tokens=30,
        cached_tokens=100,
        reasoning_tokens=10,
        finish_reasons=["stop"],
        agent_name="loan_agent",
        conversation_id="s-1",
        latency_ms=12.3456,
        caller=caller,
        dimensions={"loan_product": "fixed_30y"},
        timestamp=datetime(2026, 1, 15, 14, 3, 7, 412000, tzinfo=timezone.utc),
    )
    assert capture.records == [record]
    assert_valid_usage(record)
    assert record == {
        "schema": "esmeralda.usage",
        "schema_version": 1,
        "event": "llm.call",
        "timestamp": "2026-01-15T14:03:07.412Z",
        "source": "workload",
        "workload": {"name": "loan-agent", "runtime": "cloud_run", "framework": "custom"},
        "caller": {"verified": False, "declared": {"project_id": "example-cx-project", "agent_name": "cx"}},
        "gen_ai": {
            "system": "gcp.vertex_ai",
            "operation.name": "generate_content",
            "request.model": "gemini-flash",
            "response.model": "gemini-flash-001",
            "response.finish_reasons": ["stop"],
            "usage.input_tokens": 120,
            "usage.output_tokens": 30,
            "usage.cached_tokens": 100,
            "usage.reasoning_tokens": 10,
            "agent.name": "loan_agent",
            "conversation.id": "s-1",
        },
        "status": "ok",
        "dimensions": {"loan_product": "fixed_30y"},
        "latency_ms": 12.35,
    }


def test_records_carry_the_current_trace(recorder, assert_valid_usage):
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        record = recorder.record_tool_call(name="verify_income")
    ctx = span.get_span_context()
    assert record["trace_id"] == format(ctx.trace_id, "032x")
    assert record["span_id"] == format(ctx.span_id, "016x")
    assert "trace_id" not in recorder.record_tool_call(name="verify_income")  # no active span: omitted
    assert_valid_usage(record)


@pytest.mark.parametrize(
    ("given", "status"), [("SUCCESS", "ok"), ("ok", "ok"), ("ERROR", "error"), ("failed", "error")]
)
def test_tool_status_is_normalized(recorder, given, status, assert_valid_usage):
    record = recorder.record_tool_call(name="t", status=given, error_type="TimeoutError", duration_ms=-5)
    assert record["tool"]["status"] == record["status"] == status
    assert record["tool"]["duration_ms"] == 0.0
    assert ("error_type" in record["tool"]) is (status == "error")
    assert_valid_usage(record)


def test_agent_invocation_record(recorder, assert_valid_usage):
    record = recorder.record_agent_invocation(agent_name="loan_agent", latency_ms=99, input_tokens=5)
    assert record["gen_ai"] == {"operation.name": "invoke_agent", "agent.name": "loan_agent", "usage.input_tokens": 5}
    assert_valid_usage(record)


def test_unknown_event_is_rejected(recorder):
    with pytest.raises(ValueError):
        recorder.build("llm")


def test_recorder_dimensions_merge(capture, assert_valid_usage):
    rec = usage.UsageRecorder(capture, workload=usage.Workload("a"), dimensions={"country": "us", "tier": "gold"})
    record = rec.record_tool_call(name="t", dimensions={"tier": "silver"})
    assert record["dimensions"] == {"country": "us", "tier": "silver"}
    assert_valid_usage(record)


def test_string_map_normalization():
    values = {"loan_product": 30, "Bad-Key": "x", "empty": " ", "none": None, "long": "y" * 500, "api_key": "k"}
    assert usage.string_map(values) == {"loan_product": "30", "long": "y" * 256}
    assert len(usage.string_map({f"k{i}": "v" for i in range(50)})) == 32


def test_transport_errors_never_raise(caplog):
    class Broken:
        def emit(self, record):
            raise OSError("disk full")

    rec = usage.UsageRecorder(Broken(), workload=usage.Workload("a"))
    assert rec.record_tool_call(name="t")["tool"]["name"] == "t"
    assert "Failed to emit usage record" in caplog.text


def test_module_level_api_uses_the_process_recorder(recorder, capture):
    usage.set_recorder(recorder)
    usage.record_llm_call(request_model="m")
    usage.record_tool_call(name="t")
    usage.record_agent_invocation(agent_name="a")
    assert [r["event"] for r in capture.records] == ["llm.call", "tool.call", "agent.invocation"]
    assert usage.get_recorder() is recorder


# ---- workload --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "runtime"),
    [
        ({"GOOGLE_CLOUD_AGENT_ENGINE_ID": "123"}, "agent_engine"),
        ({"K_SERVICE": "svc"}, "cloud_run"),
        ({"KUBERNETES_SERVICE_HOST": "10.0.0.1"}, "gke"),
        ({}, "external"),
        ({"ESMERALDA_RUNTIME": "gke", "K_SERVICE": "svc"}, "gke"),
    ],
)
def test_runtime_detection(monkeypatch, env, runtime):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert usage.detect_runtime() == runtime


def test_workload_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_NAME", "loan_agent")
    monkeypatch.setenv("ESMERALDA_TEAM", "ai-coe")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "123")
    assert usage.Workload.from_env(framework="ADK").to_dict() == {
        "name": "loan_agent",
        "team": "ai-coe",
        "runtime": "agent_engine",
        "framework": "adk",
        "cloud": "gcp",
    }
    monkeypatch.setenv("ESMERALDA_WORKLOAD_NAME", "loan-agent")
    monkeypatch.setenv("ESMERALDA_CLOUD", "mars")
    workload = usage.Workload.from_env(name="explicit")
    assert (workload.name, workload.cloud) == ("loan-agent", "other")
    assert usage.Workload.from_env().to_dict()["name"] == "loan-agent"


def test_workload_defaults_when_nothing_is_set():
    assert usage.Workload.from_env().to_dict() == {"name": "unknown_workload", "runtime": "external", "cloud": "gcp"}


def test_workload_factory_is_resolved_per_record(capture, monkeypatch):
    rec = usage.UsageRecorder(capture, workload=lambda: usage.Workload.from_env(framework="adk"))
    monkeypatch.setenv("AGENT_NAME", "first")
    assert rec.record_tool_call(name="t")["workload"]["name"] == "first"
    monkeypatch.setenv("AGENT_NAME", "second")
    assert rec.record_tool_call(name="t")["workload"]["name"] == "second"


# ---- redaction -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "id-token",
        "refresh.token",
        "user_auth_token",
        "temp:user_auth_token",
        "delegated_token",
        "Authorization",
        "api_key",
        "x-api-key",
        "client_secret",
        "password",
        "credentials",
        "cookie",
        "private_key",
        "token_type",
    ],
)
def test_secret_keys(key):
    assert usage.is_secret_key(key)


@pytest.mark.parametrize(
    "key",
    ["usage.input_tokens", "usage.cached_tokens", "prompt_tokens", "tokens", "total_tokens", "loan_product", "name"],
)
def test_usage_keys_are_not_secrets(key):
    assert not usage.is_secret_key(key)


def test_configured_user_credential_key_is_secret(monkeypatch):
    assert not usage.is_secret_key("crm_grant")
    monkeypatch.setenv("USER_AUTH_ID", "crm_grant")
    assert usage.is_secret_key("crm_grant") and usage.is_secret_key("temp:crm_grant")


def test_redact_drops_secret_keys_and_masks_secret_values():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbmF0dXJl"
    api_key = "AI" + "za" + "SyA1234567890abcdefghijklmnopqrstu"  # split so secret scanners stay quiet
    oauth = "ya29" + "."  # split so secret scanners stay quiet
    value = {
        "dimensions": {"loan_product": "fixed", "user_auth_token": oauth + "abc"},
        "caller": {"declared": {"note": f"Bearer {jwt}"}},
        "list": [oauth + "a0AfH6SMB-secret", "plain"],
        "error": f"failed with key {api_key}",
        "count": 3,
    }
    assert usage.redact(value) == {
        "dimensions": {"loan_product": "fixed"},
        "caller": {"declared": {"note": usage.REDACTED}},
        "list": [usage.REDACTED, "plain"],
        "error": f"failed with key {usage.REDACTED}",
        "count": 3,
    }


def test_records_never_carry_the_user_credential(recorder, capture):
    secret = "ya29" + ".delegated-user-credential"  # split so secret scanners stay quiet
    recorder.record_llm_call(
        request_model="m",
        caller=usage.Caller(declared={"user_auth_token": secret, "project_id": "p"}),
        dimensions={"user_auth_token": secret, "note": secret},
    )
    out = json.dumps(capture.records)
    assert "delegated-user-credential" not in out
    assert capture.records[0]["caller"]["declared"] == {"project_id": "p"}


def test_redact_masks_given_secrets_wherever_they_appear(recorder, capture):
    opaque = "opaque-canary-91ab"
    assert usage.redact({"note": f"x {opaque} y", "items": [opaque]}, [opaque, None]) == {
        "note": "x [REDACTED] y",
        "items": ["[REDACTED]"],
    }
    recorder.record_tool_call(name="t", dimensions={"loan_product": opaque}, secrets=[opaque])
    assert opaque not in json.dumps(capture.records)


def test_legacy_events_are_redacted_too():
    stream = io.StringIO()
    TelemetryEmitter(stream).emit({"event": "x", "access_token": "t", "error_reason": "Bearer abc.def"})
    assert json.loads(stream.getvalue()) == {"event": "x", "error_reason": usage.REDACTED}


# ---- transports ------------------------------------------------------------------------------


def test_stdout_transport_writes_one_json_line_per_record(capsys):
    usage.StdoutTransport().emit({"schema": "esmeralda.usage", "n": 1})
    usage.StdoutTransport().emit({"schema": "esmeralda.usage", "n": 2})
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]


def test_multi_transport_isolates_failures(capture, caplog):
    class Broken:
        def emit(self, record):
            raise RuntimeError("down")

    usage.MultiTransport(Broken(), capture).emit({"a": 1})
    assert capture.records == [{"a": 1}]
    assert "Usage transport Broken failed" in caplog.text


def _in_memory_logs():
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs import export as log_export

    exporter_cls = getattr(log_export, "InMemoryLogRecordExporter", None) or getattr(log_export, "InMemoryLogExporter")
    exporter = exporter_cls()
    provider = LoggerProvider()
    provider.add_log_record_processor(log_export.SimpleLogRecordProcessor(exporter))
    return provider, exporter


def test_otel_logs_transport(recorder, assert_valid_usage):
    provider, exporter = _in_memory_logs()
    rec = usage.UsageRecorder(usage.OTelLogsTransport(provider), workload=usage.Workload("a"))
    sent = rec.record_llm_call(request_model="gemini-flash", input_tokens=3)
    (log,) = exporter.get_finished_logs()
    record = getattr(log, "log_record", log)
    assert dict(record.body) | {"gen_ai": dict(record.body["gen_ai"])} == sent
    assert dict(record.attributes) == {"schema": "esmeralda.usage", "schema_version": 1, "event": "llm.call"}
    assert_valid_usage(sent)


@pytest.mark.parametrize(
    ("value", "kinds"),
    [
        (None, ["StdoutTransport"]),
        ("stdout", ["StdoutTransport"]),
        ("otel", ["OTelLogsTransport"]),
        ("stdout, otel", ["StdoutTransport", "OTelLogsTransport"]),
        ("carrier-pigeon", ["StdoutTransport"]),
    ],
)
def test_transport_from_env(monkeypatch, value, kinds):
    if value is not None:
        monkeypatch.setenv(usage.TRANSPORT_ENV, value)
    transport = usage.transport_from_env()
    found = transport.transports if isinstance(transport, usage.MultiTransport) else (transport,)
    assert [type(t).__name__ for t in found] == kinds


# ---- OpenTelemetry GenAI span adapter ----------------------------------------------------------


@pytest.fixture
def traced(recorder):
    provider = TracerProvider()
    provider.add_span_processor(BaggageSpanProcessor())
    provider.add_span_processor(usage.UsageSpanProcessor(recorder))
    return provider.get_tracer("test")


def test_adapter_maps_genai_spans(traced, capture, assert_valid_usage):
    caller = context.CallerContext("example-cx-project", "cx_agent")
    token = context.attach_baggage(caller)
    try:
        with traced.start_as_current_span(
            "invoke_agent loan_agent",
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "loan_agent",
                "gen_ai.conversation.id": "s-1",
                "esmeralda.dimension.loan_product": "fixed_30y",
            },
        ):
            with traced.start_as_current_span(
                "generate_content gemini-flash",
                attributes={
                    "gen_ai.operation.name": "generate_content",
                    "gen_ai.provider.name": "gcp.vertex_ai",
                    "gen_ai.request.model": "gemini-flash",
                    "gen_ai.response.model": "gemini-flash-001",
                    "gen_ai.response.finish_reasons": ("stop",),
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 20,
                    "gen_ai.usage.cache_read.input_tokens": 64,
                    "gen_ai.usage.reasoning.output_tokens": 8,
                },
            ):
                pass
            with traced.start_as_current_span("call_llm", attributes={"gen_ai.usage.input_tokens": 100}):
                pass  # ADK's legacy span duplicates generate_content: ignored
            with traced.start_as_current_span("unrelated"):
                pass
            with traced.start_as_current_span(
                "execute_tool verify_income",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "verify_income",
                    "gen_ai.tool.call.id": "call-1",
                    "server.address": "income-verification.example.internal",
                    "error.type": "TimeoutError",
                },
            ) as tool_span:
                tool_span.set_status(Status(StatusCode.ERROR, "slow"))
    finally:
        context.detach_baggage(token)

    llm, tool, agent = capture.records
    for record in capture.records:
        assert_valid_usage(record)
        assert record["caller"] == {
            "verified": False,
            "declared": {"project_id": "example-cx-project", "agent_name": "cx_agent"},
        }
        assert record["trace_id"] == llm["trace_id"]
    assert llm["event"] == "llm.call"
    assert llm["gen_ai"] == {
        "system": "gcp.vertex_ai",
        "operation.name": "generate_content",
        "request.model": "gemini-flash",
        "response.model": "gemini-flash-001",
        "response.finish_reasons": ["stop"],
        "usage.input_tokens": 100,
        "usage.output_tokens": 20,
        "usage.cached_tokens": 64,
        "usage.reasoning_tokens": 8,
    }
    assert llm["latency_ms"] >= 0
    assert tool["event"] == "tool.call" and tool["status"] == "error"
    assert tool["tool"]["name"] == "verify_income"
    assert tool["tool"]["status"] == "error" and tool["tool"]["error_type"] == "TimeoutError"
    assert tool["tool"]["server"] == "income-verification.example.internal"
    assert tool["tool"]["call_id"] == "call-1"
    assert agent["event"] == "agent.invocation"
    assert agent["gen_ai"]["agent.name"] == "loan_agent" and agent["gen_ai"]["conversation.id"] == "s-1"
    assert agent["dimensions"] == {"loan_product": "fixed_30y"}


def test_adapter_falls_back_to_span_names(traced, capture, assert_valid_usage):
    with traced.start_as_current_span("execute_tool lookup", attributes={"gen_ai.operation.name": "execute_tool"}):
        pass
    with traced.start_as_current_span("invoke_agent helper", attributes={"gen_ai.operation.name": "invoke_agent"}):
        pass
    with traced.start_as_current_span("chat", attributes={"gen_ai.operation.name": "chat"}):
        pass
    tool, agent, llm = capture.records
    assert tool["tool"]["name"] == "lookup" and tool["tool"]["status"] == "ok"
    assert agent["gen_ai"]["agent.name"] == "helper"
    assert llm["gen_ai"]["request.model"] == "unknown"
    for record in capture.records:
        assert_valid_usage(record)


def test_adapter_errors_do_not_break_tracing(caplog):
    class Broken:
        def emit(self, record):
            raise RuntimeError("down")

    provider = TracerProvider()
    provider.add_span_processor(usage.UsageSpanProcessor(usage.UsageRecorder(Broken(), workload=usage.Workload("a"))))
    with provider.get_tracer("t").start_as_current_span("chat", attributes={"gen_ai.operation.name": "chat"}):
        pass
    assert "Failed to emit usage record" in caplog.text


def test_adapter_framework_shortcut(monkeypatch):
    monkeypatch.setenv("AGENT_NAME", "graph_agent")
    processor = usage.UsageSpanProcessor(framework="langgraph")
    assert processor._recorder.workload.framework == "langgraph"
