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

"""Validate real Envoy access logs against the shared usage contract (#151)."""

import base64
import json
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest
from fakes import bearer

HOST = "legacy-dms.esmeralda.internal"
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


@pytest.fixture(scope="module")
def validator():
    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts/usage-event.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def validate_usage_log(validator, record):
    # Cloud Logging promotes severity to the LogEntry envelope.
    assert record["severity"] == "INFO"
    validator.validate({key: value for key, value in record.items() if key != "severity"})


def request_record(gateway, validator, *, host=HOST, headers=None, query="", body=None):
    path = f"/usage/{uuid.uuid4().hex}"
    lua_errors = gateway.envoy.counter("http.ingress.lua.errors")
    response = gateway.post(host, path + query, headers=headers or {}, json=body or {})
    record = gateway.envoy.access_log_entry(path)
    validate_usage_log(validator, record)
    assert gateway.envoy.counter("http.ingress.lua.errors") == lua_errors
    return response, record


def token(claims):
    return token_payload(json.dumps(claims))


def token_payload(payload):
    def encode(value):
        return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

    # Local tests simulate Cloud Run's prior IAM validation; this is not a valid credential.
    header = encode(json.dumps({"alg": "RS256"}))
    return f"{header}.{encode(payload)}.SIGNATURE_REMOVED_BY_GOOGLE"


@pytest.fixture(scope="module")
def iam_gateway(start_variant):
    return start_variant("usage-cloud-run", token_validated_by_cloud_run_iam=True)


def test_record_matches_usage_contract(gateway, mcp_upstream, validator):
    mcp_upstream.delay_s = 0.05
    started = datetime.now(timezone.utc)
    response, record = request_record(gateway, validator, headers={"traceparent": TRACEPARENT})
    assert response.status_code == 200
    assert (record["schema"], record["schema_version"], record["event"], record["source"]) == (
        "esmeralda.usage",
        1,
        "gateway.request",
        "gateway",
    )
    assert record["gateway"]["product"] == "envoy"
    assert record["workload"]["name"] == "legacy-dms"
    assert record["http"]["request.method"] == "POST"
    assert record["http"]["response.status_code"] == 200
    assert record["http"]["route"] == response.request.url.path
    assert record["trace_id"] == TRACE_ID
    assert "span_id" not in record  # traceparent carries the caller's span, not an Envoy span
    assert record["status"] == "ok"
    assert "error_type" not in record
    assert type(record["latency_ms"]) in (int, float) and record["latency_ms"] >= 0
    assert record["latency_ms"] >= 40  # milliseconds, including the upstream's 50 ms delay
    assert started <= datetime.fromisoformat(record["timestamp"]) <= datetime.now(timezone.utc)
    assert record["caller"] == {"verified": False}
    sinks = (
        Path(__file__).resolve().parents[2] / "infrastructure/modules/4-governance/modules/1_telemetry_sinks/main.tf"
    )
    match = re.search(r'usage_sink_filter\s*=\s*("(?:\\.|[^"\\])*")', sinks.read_text())
    assert match is not None
    assert json.loads(match[1]) == f'jsonPayload.schema="{record["schema"]}"'


def test_usage_records_are_single_line_json_on_stdout(iam_gateway, mcp_upstream, validator):
    paths = []
    for _ in range(2):
        response, record = request_record(
            iam_gateway,
            validator,
            headers={"Authorization": "Bearer " + token({"sub": 'caller"with\nnewline'})},
        )
        assert response.status_code == 200
        assert record["caller"] == {"principal": 'caller"with\nnewline', "verified": True}
        paths.append(record["http"]["route"])

    # Parse each physical stdout line independently, as Cloud Logging does. An escaped newline
    # in a claim must remain inside that line, rather than splitting the record across lines.
    records = [json.loads(line) for line in iam_gateway.envoy.log_file.read_text().splitlines()]
    matching = [record for record in records if record.get("http", {}).get("route") in paths]
    assert len(matching) == len(paths)
    for record in matching:
        assert record["schema"] == "esmeralda.usage"
        validate_usage_log(validator, record)
    assert all(path not in iam_gateway.envoy.stderr_file.read_text() for path in paths)


@pytest.mark.parametrize(
    "traceparent",
    [
        None,
        "garbage",
        f"00-{'0' * 32}-b7ad6b7169203331-01",
        f"00-{TRACE_ID}-{'0' * 16}-01",
        TRACEPARENT.upper(),
        "ff" + TRACEPARENT[2:],
        TRACEPARENT + "-extra",
    ],
)
def test_invalid_or_missing_trace_is_omitted(gateway, mcp_upstream, validator, traceparent):
    _, record = request_record(gateway, validator, headers={} if traceparent is None else {"traceparent": traceparent})
    assert "trace_id" not in record


@pytest.mark.parametrize(
    "claims, principal",
    [
        ({"email": "Caller@Example.COM", "sub": "123"}, "caller@example.com"),
        ({"sub": "spiffe://agents.example/resources/123"}, "principal://agents.example/resources/123"),
        ({"email": "unverified@example.com", "email_verified": False, "sub": "123"}, "123"),
        ({"sub": 'a"quoted\\principal'}, 'a"quoted\\principal'),
    ],
)
def test_cloud_run_caller_survives_token_swap(iam_gateway, mcp_upstream, validator, claims, principal):
    credential = token(claims)
    _, record = request_record(iam_gateway, validator, headers={"Authorization": "Bearer " + credential})
    assert record["caller"] == {"principal": principal, "verified": True}
    assert bearer(mcp_upstream.last) != credential
    assert credential not in json.dumps(record)


@pytest.mark.parametrize(
    ("payload", "principal"),
    [
        (r'{"sub":"a\"quoted\\and\/slash,}:value"}', 'a"quoted\\and/slash,}:value'),
        (r'{"\u0073ub":"\u00e9\u5b57\ud83d\ude00","iat":1.7e9,"exp":1700000060}', "é字😀"),
        (' { "email": "ignore@example.com", "email_verified": false, "sub": "123", "nbf": -1.5E+2 } ', "123"),
    ],
)
def test_scalar_claims_decode_json_escapes(iam_gateway, mcp_upstream, validator, payload, principal):
    _, record = request_record(iam_gateway, validator, headers={"Authorization": "Bearer " + token_payload(payload)})
    assert record["caller"] == {"principal": principal, "verified": True}


@pytest.mark.parametrize(
    "payload",
    [
        '{"sub":"caller",}',
        '{"sub":"caller"} trailing',
        '{"sub":"caller\nwithout an escape"}',
        r'{"sub":"caller\q"}',
        r'{"sub":"caller\u12zz"}',
        r'{"sub":"caller\ud83d"}',
        r'{"sub":"caller\ude00"}',
        '{"sub":"caller","iat":01}',
        '{"sub":"caller","iat":1.}',
        '{"sub":"caller","iat":1e}',
        '{"sub":"caller","email_verified":trueish}',
        '{"sub":"caller","other":{"sub":"unrelated"}}',
        '{"sub":"first","sub":"second"}',
    ],
)
def test_malformed_or_non_flat_claims_remain_unverified(iam_gateway, mcp_upstream, validator, payload):
    response, record = request_record(
        iam_gateway, validator, headers={"Authorization": "Bearer " + token_payload(payload)}
    )
    assert response.status_code == 200
    assert record["caller"] == {"verified": False}


@pytest.mark.parametrize("signature", ["synthetic-signature", ""])
def test_cloud_run_token_may_retain_or_strip_its_signature(iam_gateway, mcp_upstream, validator, signature):
    credential = token({"email": "signed@example.com"}).replace("SIGNATURE_REMOVED_BY_GOOGLE", signature)
    _, record = request_record(iam_gateway, validator, headers={"Authorization": "Bearer " + credential})
    assert record["caller"] == {"principal": "signed@example.com", "verified": True}


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": 123},
        {"email": False},
        {"sub": " "},
        {"sub": "a" * 513},
        {"sub": "spiffe://" + "a" * 503},
        {"sub": "a" * 9000},
    ],
)
def test_invalid_or_oversized_principal_is_not_attributed(iam_gateway, mcp_upstream, validator, claims):
    _, record = request_record(iam_gateway, validator, headers={"Authorization": "Bearer " + token(claims)})
    assert record["caller"] == {"verified": False}


@pytest.mark.parametrize("serverless", ["", "malformed", token({"email": "selected@example.com"})])
def test_serverless_header_takes_precedence(iam_gateway, mcp_upstream, validator, serverless):
    _, record = request_record(
        iam_gateway,
        validator,
        headers={
            "X-Serverless-Authorization": serverless,
            "Authorization": "Bearer " + token({"email": "ignored@example.com"}),
        },
    )
    expected = (
        {"verified": False}
        if serverless in ("", "malformed")
        else {"principal": "selected@example.com", "verified": True}
    )
    assert record["caller"] == expected


def test_local_tokens_and_forged_identity_headers_are_not_verified(gateway, mcp_upstream, validator):
    _, record = request_record(
        gateway,
        validator,
        headers={
            "Authorization": "Bearer " + token({"email": "forged@example.com"}),
            "X-Agent-Spiffe-Id": "spiffe://forged",
            "X-Esmeralda-Billed-Team": "forged",
        },
    )
    assert record["caller"] == {"verified": False}


@pytest.mark.parametrize("credential", ["not-a-jwt", "a.e30.b", "a.W10.b", "a.b.b"])
def test_unusable_identity_remains_unverified(iam_gateway, mcp_upstream, validator, credential):
    response, record = request_record(iam_gateway, validator, headers={"Authorization": "Bearer " + credential})
    assert response.status_code == 200
    assert record["caller"] == {"verified": False}


def test_credentials_query_and_body_are_absent(gateway, mcp_upstream, validator):
    secrets = ["synthetic-api-key", "synthetic-user-secret", "synthetic-query-secret", "synthetic-body-secret"]
    _, record = request_record(
        gateway,
        validator,
        headers={"X-API-Key": secrets[0], "User-Auth-Token": secrets[1]},
        query="?access_token=" + secrets[2],
        body={"prompt": secrets[3]},
    )
    assert all(secret not in json.dumps(record) for secret in secrets)
    assert "?" not in record["http"]["route"]


def test_local_404_is_recorded(gateway, validator):
    response, record = request_record(gateway, validator, host="unknown.esmeralda.internal")
    assert response.status_code == record["http"]["response.status_code"] == 404
    assert record["status"] == "error"
    assert record["error_type"] == "NR"


def test_rate_limited_request_is_recorded(start_variant, mcp_upstream, validator):
    gateway = start_variant("usage-rate-limit", rate_limit_per_minute=1)
    headers = {"X-API-Key": uuid.uuid4().hex}
    request_record(gateway, validator, headers=headers)
    response, record = request_record(gateway, validator, headers=headers)
    assert response.status_code == record["http"]["response.status_code"] == 429
    assert record["status"] == "error"
    assert record["error_type"] == "RL"


def test_tls_failure_is_recorded(gateway, upstreams, validator):
    response, record = request_record(gateway, validator, host="untrusted.esmeralda.internal")
    assert response.status_code == record["http"]["response.status_code"] == 503
    assert record["status"] == "error"
    assert "UF" in record["error_type"].split(",")


def test_disconnected_request_has_no_zero_http_status(gateway, mcp_upstream, validator):
    """A disconnected caller received no HTTP response: Envoy's internal code 0 is not an HTTP status."""
    path = f"/usage/{uuid.uuid4().hex}"
    mcp_upstream.delay_s = 1
    with socket.create_connection(("127.0.0.1", gateway.envoy.listener_port), timeout=5) as connection:
        connection.sendall(f"POST {path} HTTP/1.1\r\nHost: {HOST}\r\nContent-Length: 0\r\n\r\n".encode())
        deadline = time.monotonic() + 5
        while not mcp_upstream.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert mcp_upstream.requests, "the request never reached the upstream"
    record = gateway.envoy.access_log_entry(path)
    validate_usage_log(validator, record)
    assert "response.status_code" not in record["http"]
    assert record["status"] == "error"
