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

import otel_setup

# Patch telemetry before importing main to avoid GCP API calls during testing.
with patch("otel_setup.init_telemetry", return_value=MagicMock()):
    from main import _read, _send


def test_send_returns_status_and_message_id():
    result = _send("user@example.com", "Test Subject", "Test Body")
    assert result["status"] == "sent"
    assert "message_id" in result
    assert result["to"] == "user@example.com"
    assert result["subject"] == "Test Subject"


def test_read_returns_all_emails():
    result = _read()
    assert len(result["emails"]) == 3


def test_read_with_specific_id():
    result = _read("msg-001")
    assert len(result["emails"]) == 1
    assert result["emails"][0]["id"] == "msg-001"


def test_read_with_unknown_id():
    result = _read("msg-999")
    assert len(result["emails"]) == 0


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
