# Copyright 2025 Google LLC
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

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from starlette.testclient import TestClient

import otel_setup

# Patch telemetry before importing main to avoid GCP API calls during testing.
with patch("otel_setup.init_telemetry", return_value=MagicMock()):
    from main import _verify, app


def test_verify_known_applicant():
    result = _verify("Julian", "Sterling")
    assert result is not None
    assert result["status"] == "success"
    assert result["verification"]["applicant"]["name"] == "Julian A. Sterling"


def test_verify_unknown_applicant():
    result = _verify("Unknown", "Person")
    assert result is None


def test_verify_case_insensitive():
    result = _verify("JULIAN", "STERLING")
    assert result is not None
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------

client = TestClient(app)


def test_rest_health():
    response = client.get("/api/income-verification/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rest_verify_known_applicant():
    response = client.post(
        "/api/income-verification/verify",
        json={"first_name": "Julian", "last_name": "Sterling"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["verification"]["applicant"]["name"] == "Julian A. Sterling"


def test_rest_verify_unknown_applicant():
    response = client.post(
        "/api/income-verification/verify",
        json={"first_name": "Unknown", "last_name": "Person"},
    )
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == "error"


def test_rest_verify_case_insensitive():
    response = client.post(
        "/api/income-verification/verify",
        json={"first_name": "JULIAN", "last_name": "STERLING"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"


# ---------------------------------------------------------------------------
# OTLP export target (otel_setup._exporter_kwargs)
# ---------------------------------------------------------------------------

def _no_google_credentials():
    raise AssertionError("a collector endpoint must not mint Google credentials")


@pytest.mark.parametrize("value", [None, "", "  "])
def test_exporter_without_endpoint_uses_google_telemetry_api(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", value)
    creds = object()
    monkeypatch.setattr(otel_setup, "_get_channel_credentials", lambda: creds)
    assert otel_setup._exporter_kwargs() == {"endpoint": "telemetry.googleapis.com:443", "credentials": creds}


def test_exporter_uses_plaintext_collector_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.example:4317")
    monkeypatch.setattr(otel_setup, "_get_channel_credentials", _no_google_credentials)
    assert otel_setup._exporter_kwargs() == {"endpoint": "http://otel-collector.example:4317", "insecure": True}


def test_exporter_uses_tls_collector_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel-collector.example:4317")
    monkeypatch.setattr(otel_setup, "_get_channel_credentials", _no_google_credentials)
    assert otel_setup._exporter_kwargs() == {"endpoint": "https://otel-collector.example:4317", "insecure": False}


def test_exporters_accept_the_collector_arguments(monkeypatch):
    # The gRPC channel is lazy: building the exporters makes no network call.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    kwargs = otel_setup._exporter_kwargs()
    OTLPSpanExporter(**kwargs).shutdown()
    OTLPMetricExporter(**kwargs).shutdown()
