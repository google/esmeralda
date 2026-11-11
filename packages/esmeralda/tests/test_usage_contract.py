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

"""CI validation of the usage contract: the schema, its examples, and the library's records."""

import copy
import io
import json

import pytest
from conftest import CONTRACTS
from jsonschema import Draft202012Validator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode

from esmeralda import usage

EXAMPLES = sorted((CONTRACTS / "examples").glob("*.json"))


def load(path):
    return json.loads(path.read_text())


# ---- schema and examples ---------------------------------------------------------------------


def test_schema_is_valid_draft_2020_12(usage_schema):
    Draft202012Validator.check_schema(usage_schema)
    assert usage_schema["properties"]["schema"]["const"] == usage.SCHEMA
    assert usage_schema["properties"]["schema_version"]["const"] == usage.SCHEMA_VERSION
    assert tuple(usage_schema["properties"]["event"]["enum"]) == usage.EVENTS


def test_there_is_one_example_per_event():
    assert sorted(load(p)["event"] for p in EXAMPLES) == sorted(usage.EVENTS)
    for path in EXAMPLES:
        assert path.name == f"{load(path)['event']}.json"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_validate(path, assert_valid_usage):
    assert_valid_usage(load(path))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_already_redacted(path):
    record = load(path)
    assert usage.redact(record) == record


def mutate(path, change):
    record = copy.deepcopy(load(CONTRACTS / "examples" / path))
    change(record)
    return record


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(mutate("llm.call.json", lambda r: r.update(schema_version=2)), id="future-version"),
        pytest.param(mutate("llm.call.json", lambda r: r.update(schema="other")), id="other-schema"),
        pytest.param(mutate("llm.call.json", lambda r: r.update(event="llm")), id="unknown-event"),
        pytest.param(mutate("llm.call.json", lambda r: r.update(extra=1)), id="unknown-top-level-field"),
        pytest.param(mutate("llm.call.json", lambda r: r.pop("workload")), id="workload-source-without-workload"),
        pytest.param(mutate("llm.call.json", lambda r: r.pop("gen_ai")), id="llm-call-without-gen-ai"),
        pytest.param(
            mutate("llm.call.json", lambda r: [r["gen_ai"].pop(k) for k in ("request.model", "response.model")]),
            id="llm-call-without-model",
        ),
        pytest.param(mutate("llm.call.json", lambda r: r["gen_ai"].update({"usage.input_tokens": -1})), id="neg-count"),
        pytest.param(mutate("llm.call.json", lambda r: r["gen_ai"].update(tokens=5)), id="unknown-gen-ai-field"),
        pytest.param(mutate("llm.call.json", lambda r: r["dimensions"].update(loan_product=3)), id="non-string-dim"),
        pytest.param(mutate("llm.call.json", lambda r: r["dimensions"].update({"Bad-Key": "x"})), id="bad-dim-key"),
        pytest.param(mutate("llm.call.json", lambda r: r["caller"].pop("verified")), id="caller-without-verified"),
        pytest.param(mutate("llm.call.json", lambda r: r.update(trace_id="unknown_trace")), id="bad-trace-id"),
        pytest.param(mutate("llm.call.json", lambda r: r.update(timestamp="yesterday")), id="bad-timestamp"),
        pytest.param(mutate("llm.call.json", lambda r: r["workload"].update(runtime="vm")), id="unknown-runtime"),
        pytest.param(mutate("tool.call.json", lambda r: r.pop("tool")), id="tool-call-without-tool"),
        pytest.param(mutate("tool.call.json", lambda r: r["tool"].update(status="SUCCESS")), id="legacy-status"),
        pytest.param(mutate("agent.invocation.json", lambda r: r["gen_ai"].pop("agent.name")), id="agent-no-name"),
        pytest.param(mutate("gateway.request.json", lambda r: r.update(source="workload")), id="gateway-as-workload"),
        pytest.param(mutate("gateway.request.json", lambda r: r.pop("gateway")), id="gateway-without-gateway"),
    ],
)
def test_contract_rejects(record, usage_validator):
    assert not usage_validator.is_valid(record)


# ---- every record the library produces ------------------------------------------------------


@pytest.fixture
def recorder():
    stream = io.StringIO()
    rec = usage.UsageRecorder(
        usage.StdoutTransport(stream), workload=usage.Workload("loan-agent", team="ai-coe", runtime="cloud_run")
    )
    rec.lines = lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    return rec


def test_manual_api_records_validate(recorder, assert_valid_usage):
    caller = usage.Caller.from_declared({"project_id": "example-cx-project", "agent_name": "cx"})
    recorder.record_llm_call(request_model="gemini-flash", input_tokens=10, output_tokens=2, caller=caller)
    recorder.record_llm_call()  # nothing known still yields a valid record
    recorder.record_llm_call(request_model="gemini-flash", status="error", error_type="ResourceExhausted")
    recorder.record_tool_call(name="verify_income", duration_ms=12.5)
    recorder.record_tool_call(name="read_inbox", status="ERROR", error_type="TimeoutError")
    recorder.record_agent_invocation(agent_name="loan_agent", latency_ms=30, input_tokens=10, output_tokens=2)
    recorder.emit(
        recorder.build(
            "gateway.request",
            gateway={"product": "envoy"},
            http={"request.method": "POST", "response.status_code": 200},
            workload=usage.Workload("loan-agent"),
        )
        | {"source": "gateway"}
    )
    records = recorder.lines()
    assert len(records) == 7
    for record in records:
        assert_valid_usage(record)


def test_span_adapter_records_validate(recorder, assert_valid_usage):
    provider = TracerProvider()
    provider.add_span_processor(usage.UsageSpanProcessor(recorder))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("invoke_agent loan_agent", attributes={"gen_ai.operation.name": "invoke_agent"}):
        with tracer.start_as_current_span(
            "generate_content gemini-flash",
            attributes={"gen_ai.operation.name": "generate_content", "gen_ai.request.model": "gemini-flash"},
        ):
            pass
        with tracer.start_as_current_span("execute_tool t", attributes={"gen_ai.operation.name": "execute_tool"}) as s:
            s.set_status(Status(StatusCode.ERROR))
    records = recorder.lines()
    assert [r["event"] for r in records] == ["llm.call", "tool.call", "agent.invocation"]
    for record in records:
        assert_valid_usage(record)
