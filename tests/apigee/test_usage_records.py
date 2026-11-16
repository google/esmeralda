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

"""ML-interaction emits valid `esmeralda.usage` records: render each template against flow variables
produced by running JS-Usage-Context (usage-context.js) and validate them against the contract."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from apigee_harness import render_message_template, run_apigee_js

REPO_ROOT = Path(__file__).resolve().parents[2]
APIGEE = REPO_ROOT / "apps" / "apigee"
# The esmeralda.usage contract, as merged: contracts/ is the single source of truth.
SCHEMA_PATH = REPO_ROOT / "contracts" / "usage-event.schema.json"

BUNDLES = {
    "llm-gateway": APIGEE / "proxies" / "llm-gateway" / "apiproxy",
    "partner-agents": APIGEE / "proxies" / "partner-agents" / "apiproxy",
    "partner-mcp": APIGEE / "proxies" / "partner-mcp" / "apiproxy",
    "usage-metering": APIGEE / "sharedflows" / "usage-metering" / "sharedflowbundle",
}
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
START_MS = 1_793_000_000_000


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def _template(bundle: str) -> str:
    root = ET.parse(BUNDLES[bundle] / "policies" / "ML-interaction.xml").getroot()
    message = root.find("./CloudLogging/Message")
    assert message is not None and message.attrib.get("contentType") == "application/json"
    return message.text or ""


def _properties(bundle: str, policy: str = "JS-Usage-Context") -> dict[str, str]:
    root = ET.parse(BUNDLES[bundle] / "policies" / f"{policy}.xml").getroot()
    assert (root.findtext("ResourceURL") or "").strip() == "jsc://usage-context.js"
    return {prop.attrib["name"]: (prop.text or "").strip() for prop in root.findall("./Properties/Property")}


def _record(bundle: str, flow: dict[str, Any], policy: str = "JS-Usage-Context") -> dict[str, Any]:
    state = run_apigee_js(BUNDLES[bundle] / "resources" / "jsc" / "usage-context.js", flow, _properties(bundle, policy))
    rendered = render_message_template(_template(bundle), state)
    return json.loads(rendered)


def _base_flow(**extra: Any) -> dict[str, Any]:
    flow: dict[str, Any] = {
        "apiproxy.name": "test-proxy",
        "organization.name": "org",
        "request.verb": "POST",
        "client.received.start.timestamp": START_MS,
        "system.timestamp": START_MS + 1234,
        "request.header.traceparent": f"00-{TRACE_ID}-00f067aa0ba902b7-01",
    }
    flow.update(extra)
    return flow


def test_usage_context_js_copies_are_identical() -> None:
    """Every bundle ships the same usage-context.js (one implementation of the record contract)."""
    copies = {name: (path / "resources" / "jsc" / "usage-context.js").read_text() for name, path in BUNDLES.items()}
    assert len(set(copies.values())) == 1, f"usage-context.js differs across bundles: {sorted(copies)}"


@pytest.mark.parametrize("bundle", sorted(BUNDLES))
def test_ml_interaction_is_body_free_and_schema_keyed(bundle: str) -> None:
    template = _template(bundle)
    for forbidden in ("request.content", "response.content", "client.id", "client_id", "apikey", "x-api-key"):
        assert forbidden not in template.lower()
    for required in ('"schema": "esmeralda.usage"', '"schema_version": 1', '"source": "gateway"', '"dimensions": {}'):
        assert required in template


def test_llm_gateway_json_call_record(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "llmgw.caller.principal": "sa-cx-orchestrator@example-project.iam.gserviceaccount.com",
            "llm.request.model": "gemini-2.5-flash",
            "llm.model": "gemini-2.5-flash-001",
            "llm.promptTokenCount": 120.0,
            "llm.candidatesTokenCount": 34.0,
            "llm.thoughtsTokenCount": 16.0,
            "llm.cachedTokenCount": 64.0,
            "response.status.code": 200,
        }
    )
    record = _record("llm-gateway", flow)
    validator.validate(record)
    assert record["event"] == "llm.call"
    assert record["trace_id"] == TRACE_ID
    assert "span_id" not in record  # the traceparent parent id is the caller's span, not this hop
    assert record["caller"] == {
        "principal": "sa-cx-orchestrator@example-project.iam.gserviceaccount.com",
        "verified": True,
    }
    assert record["gen_ai"] == {
        "system": "gcp.vertex_ai",
        "operation.name": "generate_content",
        "request.model": "gemini-2.5-flash",
        "response.model": "gemini-2.5-flash-001",
        "usage.input_tokens": 120,
        "usage.output_tokens": 50,  # candidates + reasoning (OTel GenAI)
        "usage.cached_tokens": 64,
        "usage.reasoning_tokens": 16,
    }
    assert record["http"] == {"request.method": "POST", "response.status_code": 200}
    assert record["status"] == "ok"
    assert record["latency_ms"] == 1234
    assert record["gateway"] == {"product": "apigee", "name": "test-proxy"}
    assert record["dimensions"] == {}


def test_llm_gateway_rejected_caller_record(validator: jsonschema.Draft202012Validator) -> None:
    """A rejected call (DefaultFaultRule) still yields a valid llm.call record, marked as an error."""
    flow = _base_flow(
        **{
            "llmgw.caller.principal": "unregistered@example-project.iam.gserviceaccount.com",
            "llm.request.model": "gemini-2.5-flash",
            "error.status.code": 403,
            "fault.name": "RaiseFault",
        }
    )
    flow.pop("request.header.traceparent")
    record = _record("llm-gateway", flow, policy="JS-Usage-Context-Fault")
    validator.validate(record)
    assert "trace_id" not in record
    assert record["status"] == "error"
    assert record["error_type"] == "RaiseFault"
    assert record["http"]["response.status_code"] == 403
    assert "usage.input_tokens" not in record["gen_ai"]


def test_llm_gateway_unauthenticated_record(validator: jsonschema.Draft202012Validator) -> None:
    """A failed ID-token verification has no principal: caller.verified is false."""
    flow = _base_flow(**{"error.status.code": 401, "fault.name": "InvalidToken"})
    record = _record("llm-gateway", flow, policy="JS-Usage-Context-Fault")
    validator.validate(record)
    assert record["caller"] == {"verified": False}
    assert record["gen_ai"]["request.model"] == "unknown"


def test_partner_agents_record(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "verifyapikey.VAK-Verify-Partner-Key.app.name": "partner-smoke-test",
            "caller.identity": "anonymous",
            "proxy.pathsuffix": "/cx-mortgage-orchestrator:streamQuery",
            "llm.model": "gemini-2.5-flash",
            "llm.promptTokenCount": 142,
            "llm.candidatesTokenCount": 58,
            "response.status.code": 200,
        }
    )
    record = _record("partner-agents", flow)
    validator.validate(record)
    assert record["event"] == "gateway.request"
    assert record["caller"] == {"principal": "partner-smoke-test", "verified": True}
    assert record["workload"] == {"name": "cx-mortgage-orchestrator", "runtime": "agent_engine", "cloud": "gcp"}
    assert record["gen_ai"]["usage.input_tokens"] == 142
    assert record["gen_ai"]["usage.output_tokens"] == 58


def test_partner_agents_prefers_verified_cross_cloud_principal(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "verifyapikey.VAK-Verify-Partner-Key.app.name": "aws-underwriter-app",
            "caller.identity": "https://issuer.example.com#workload-123",
            "proxy.pathsuffix": "/cx-mortgage-orchestrator:query",
            "response.status.code": 200,
        }
    )
    record = _record("partner-agents", flow)
    validator.validate(record)
    assert record["caller"]["principal"] == "https://issuer.example.com#workload-123"
    assert "gen_ai" not in record  # no token counts in this response


def test_partner_agents_invalid_key_record(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "proxy.pathsuffix": "/cx-mortgage-orchestrator:query",
            "error.status.code": 401,
            "fault.name": "InvalidApiKey",
        }
    )
    record = _record("partner-agents", flow, policy="JS-Usage-Context-Fault")
    validator.validate(record)
    assert record["caller"] == {"verified": False}
    assert record["status"] == "error"


def test_partner_mcp_tool_call_record(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "verifyapikey.VAK-Verify-Partner-Key.app.name": "partner-smoke-test",
            "mcp.service.host": "income-verification",
            "mcp.tool.name": "verify_income",
            "response.status.code": 200,
        }
    )
    record = _record("partner-mcp", flow)
    validator.validate(record)
    assert record["workload"] == {"name": "income-verification", "runtime": "cloud_run", "cloud": "gcp"}
    assert record["tool"] == {"name": "verify_income", "server": "income-verification", "status": "ok"}
    assert "gen_ai" not in record


def test_partner_mcp_forbidden_tool_record(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "verifyapikey.VAK-Verify-Partner-Key.app.name": "partner-smoke-test",
            "mcp.service.host": "legacy-dms",
            "mcp.tool.name": "delete_documents",
            "error.status.code": 403,
            "fault.name": "RaiseFault",
        }
    )
    record = _record("partner-mcp", flow, policy="JS-Usage-Context-Fault")
    validator.validate(record)
    assert record["tool"]["status"] == "error"
    assert record["status"] == "error"


def test_usage_metering_record(validator: jsonschema.Draft202012Validator) -> None:
    flow = _base_flow(
        **{
            "verifyapikey.VAK-Verify-Partner-Key.app.name": "partner-smoke-test",
            "llm.promptTokenCount": 10,
            "llm.candidatesTokenCount": 5,
            "response.status.code": 200,
        }
    )
    record = _record("usage-metering", flow)
    validator.validate(record)
    assert record["gen_ai"] == {"usage.input_tokens": 10, "usage.output_tokens": 5}


@pytest.mark.parametrize("traceparent", ["", "garbage", f"00-{'0' * 32}-00f067aa0ba902b7-01", "00-ABC-1-01"])
def test_invalid_traceparent_is_omitted(validator: jsonschema.Draft202012Validator, traceparent: str) -> None:
    flow = _base_flow(**{"request.header.traceparent": traceparent, "response.status.code": 200})
    record = _record("usage-metering", flow)
    validator.validate(record)
    assert "trace_id" not in record


def test_hostile_principal_is_json_escaped(validator: jsonschema.Draft202012Validator) -> None:
    """Values are JSON-encoded by usage-context.js, so a crafted principal can't break the record."""
    flow = _base_flow(
        **{"verifyapikey.VAK-Verify-Partner-Key.app.name": 'evil","verified":false,"x":"', "response.status.code": 200}
    )
    record = _record("usage-metering", flow)
    validator.validate(record)
    assert record["caller"] == {"principal": 'evil","verified":false,"x":"', "verified": True}


def test_partner_chargeback_view_selects_exactly_the_partner_records() -> None:
    """vw_partner_apigee_chargeback reads the partner proxies' records (gateway.request) and never the
    LLM gateway's llm.call records, which are internal agent usage billed in vw_usage_billable."""
    sql = (REPO_ROOT / "infrastructure/modules/4-governance/sql/vw_partner_apigee_chargeback.sql.tpl").read_text(
        encoding="utf-8"
    )
    flow = _base_flow(
        **{
            "verifyapikey.VAK-Verify-Partner-Key.app.name": "partner-smoke-test",
            "proxy.pathsuffix": "/cx-mortgage-orchestrator:query",
            "mcp.service.host": "income-verification",
            "response.status.code": 200,
        }
    )
    for bundle in ("partner-agents", "partner-mcp", "usage-metering"):
        record = _record(bundle, flow)
        assert f"AND event = '{record['event']}'" in sql, bundle
        assert f"AND source = '{record['source']}'" in sql, bundle
        assert f"AND gateway_product = '{record['gateway']['product']}'" in sql, bundle
    model_hop = _record("llm-gateway", _base_flow(**{"llmgw.caller.principal": "sa@example.iam.gserviceaccount.com"}))
    assert model_hop["event"] == "llm.call"
    assert f"event = '{model_hop['event']}'" not in sql
