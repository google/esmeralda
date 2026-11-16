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

"""Data endpoints: parameterized BigQuery with a byte cap, trace-id validation, explicit demo mode,
fail-closed catalog, and no usage emission."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from ui_testkit import API_DIR, REPO_ROOT, FakeBigQueryClient

from esmeralda_ui.app import create_app
from esmeralda_ui.auth import IAP_HEADER
from esmeralda_ui.demo import DEMO_TRACE_ID

BQ = {"bq_project": "esm-dev-governance-abcd", "bq_dataset": "esmeralda_telemetry_logs_dev"}


@pytest.fixture
def auth(keypair):
    return {IAP_HEADER: keypair.token()}


def make_client(settings, keypair, fake=None):
    return TestClient(
        create_app(settings, keys=keypair.keys, bigquery_client_factory=(lambda: fake) if fake else None),
        raise_server_exceptions=False,
    )


# ---------------------------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------------------------


def test_usage_query_is_parameterized_and_capped(cloud_settings, keypair, auth):
    fake = FakeBigQueryClient(
        {
            "vw_monthly_agent_chargeback": [
                {
                    "billing_month": datetime(2026, 10, 1, tzinfo=UTC),
                    "agent_id": "cx-mortgage-orchestrator",
                    "model": "gemini-flash",
                    "billed_team": "cx",
                    "verified": True,
                    "attribution": "verified",
                    "workload_team": "cx",
                    "total_requests": 10,
                    "total_llm_calls": 30,
                    "total_tokens": 1000,
                    "cached_prompt_tokens": 100,
                    "cache_hit_ratio_pct": 10.0,
                    "net_total_chargeback_usd": 0.5,
                    "gateway_metered_requests": 10,
                }
            ]
        }
    )
    client = make_client(cloud_settings(**BQ, bq_max_bytes_billed=12345), keypair, fake)
    response = client.get("/api/usage?month=2026-10&team=cx", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "bigquery"
    assert body["totals"] == [
        {"billed_team": "cx", "total_requests": 10, "total_tokens": 1000, "net_total_chargeback_usd": 0.5}
    ]

    (sql, config, job) = fake.jobs[0]
    assert "`esm-dev-governance-abcd.esmeralda_telemetry_logs_dev.vw_monthly_agent_chargeback`" in sql
    assert "@billing_month" in sql and "@team" in sql
    assert "cx" not in sql.replace("cx-", "")  # the value is a parameter, not in the text
    assert "2026" not in sql
    assert config.maximum_bytes_billed == 12345
    assert config.use_legacy_sql is False
    params = {p.name: (p.type_, p.value) for p in config.query_parameters}
    assert params["team"] == ("STRING", "cx")
    assert params["billing_month"] == ("TIMESTAMP", datetime(2026, 10, 1, tzinfo=UTC))
    assert job.timeout is not None


def test_every_query_is_a_select_with_parameters(cloud_settings, keypair, auth):
    fake = FakeBigQueryClient()
    client = make_client(cloud_settings(**BQ), keypair, fake)
    client.get("/api/usage", headers=auth)
    client.get("/api/audit?days=3&limit=5", headers=auth)
    client.get("/api/traces?days=2", headers=auth)
    client.get(f"/api/traces/{DEMO_TRACE_ID}", headers=auth)
    assert len(fake.jobs) == 4
    for sql, config, _ in fake.jobs:
        assert sql.lstrip().upper().startswith("SELECT")
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP"):
            assert verb not in sql.upper().split()
        assert config.maximum_bytes_billed > 0
        assert config.query_parameters
    audit_params = {p.name: p.value for p in fake.jobs[1][1].query_parameters}
    assert audit_params == {"days": 3, "limit": 5}
    trace_params = {p.name: p.value for p in fake.jobs[3][1].query_parameters}
    assert trace_params["trace_id"] == DEMO_TRACE_ID
    assert DEMO_TRACE_ID not in fake.jobs[3][0]
    assert "usage_events`" in fake.jobs[3][0]


def test_trace_waterfall_from_usage_rows(cloud_settings, keypair, auth):
    t0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
    fake = FakeBigQueryClient(
        {
            "usage_events": [
                {
                    "timestamp": t0.replace(second=2),
                    "event": "gateway.request",
                    "source": "gateway",
                    "latency_ms": 2000.0,
                },
                {"timestamp": t0.replace(second=1), "event": "llm.call", "source": "workload", "latency_ms": 500.0,
                 "input_tokens": 10, "output_tokens": 5},
            ]
        }
    )
    client = make_client(cloud_settings(**BQ), keypair, fake)
    body = client.get(f"/api/traces/{DEMO_TRACE_ID}", headers=auth).json()
    assert body["duration_ms"] == 2000.0
    assert [s["event"] for s in body["spans"]] == ["gateway.request", "llm.call"]
    assert [s["offset_ms"] for s in body["spans"]] == [0.0, 500.0]
    assert body["total_tokens"] == 15


def test_unknown_trace_is_404(cloud_settings, keypair, auth):
    client = make_client(cloud_settings(**BQ), keypair, FakeBigQueryClient())
    assert client.get(f"/api/traces/{'0' * 32}", headers=auth).status_code == 404


@pytest.mark.parametrize(
    "trace_id",
    [
        "x\" OR logName:\"projects",
        "4BF92F3577B34DA6A3CE929D0E0E4736",
        "4bf92f3577b34da6a3ce929d0e0e473",
        "4bf92f3577b34da6a3ce929d0e0e47366",
        "4bf92f3577b34da6a3ce929d0e0e473g",
        "%27%20OR%201%3D1",
    ],
)
def test_invalid_trace_ids_never_reach_bigquery(cloud_settings, keypair, auth, trace_id):
    fake = FakeBigQueryClient()
    client = make_client(cloud_settings(**BQ), keypair, fake)
    response = client.get(f"/api/traces/{trace_id}", headers=auth)
    assert response.status_code in (404, 422)
    assert fake.jobs == []


@pytest.mark.parametrize("query", ["month=2026-13", "month=26-01", "team=CX", "team=a;b", "team=' OR 1=1"])
def test_invalid_usage_filters_are_422(cloud_settings, keypair, auth, query):
    fake = FakeBigQueryClient()
    response = make_client(cloud_settings(**BQ), keypair, fake).get(f"/api/usage?{query}", headers=auth)
    assert response.status_code == 422
    assert fake.jobs == []


def test_bigquery_failure_is_generic_503(cloud_settings, keypair, auth):
    fake = FakeBigQueryClient(error=RuntimeError("Access Denied: Project esm-dev-governance-abcd"))
    response = make_client(cloud_settings(**BQ), keypair, fake).get("/api/audit", headers=auth)
    assert response.status_code == 503
    assert response.json() == {"error": "data source unavailable"}


# ---------------------------------------------------------------------------------------------
# no fake data unless asked for
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/usage", "/api/audit", "/api/traces", f"/api/traces/{DEMO_TRACE_ID}"])
def test_unconfigured_data_is_503_not_synthetic(cloud_settings, keypair, auth, path):
    response = make_client(cloud_settings(), keypair).get(path, headers=auth)
    assert response.status_code == 503
    assert "demo" not in response.text


@pytest.mark.parametrize("path", ["/api/usage", "/api/audit", "/api/traces", f"/api/traces/{DEMO_TRACE_ID}"])
def test_demo_mode_labels_every_response(cloud_settings, keypair, auth, path):
    response = make_client(cloud_settings(demo=True), keypair).get(path, headers=auth)
    assert response.status_code == 200
    assert response.json()["source"] == "demo"


def test_demo_trace_has_waterfall(cloud_settings, keypair, auth):
    body = make_client(cloud_settings(demo=True), keypair).get(f"/api/traces/{DEMO_TRACE_ID}", headers=auth).json()
    assert len(body["spans"]) >= 3
    assert body["spans"][0]["offset_ms"] == 0.0


# ---------------------------------------------------------------------------------------------
# catalog (esmeralda.catalog, fail closed)
# ---------------------------------------------------------------------------------------------


def test_catalog_comes_from_the_resolved_catalog(cloud_settings, keypair, auth):
    client = make_client(cloud_settings(), keypair)
    body = client.get("/api/catalog", headers=auth).json()
    assert body["environment"] == "dev"
    assert body["resolved_file"] == "catalog/.resolved/dev.json"
    resolved = json.loads((REPO_ROOT / "catalog/.resolved/dev.json").read_text())
    names = {t["name"] for t in body["targets"]}
    assert set(resolved["catalog"]) <= names
    specialist = next(t for t in body["targets"] if t["name"] == "income-verification")
    assert specialist["owner_team"] == "ai-coe"
    assert {c["caller"] for c in specialist["callers"]} >= {"ai-coe-mortgage-specialist"}

    detail = client.get("/api/catalog/income-verification", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["name"] == "income-verification"
    assert client.get("/api/catalog/does-not-exist", headers=auth).status_code == 404
    assert client.get("/api/catalog/Bad_Name", headers=auth).status_code == 422


def test_catalog_fails_closed_when_resolved_file_is_stale(tmp_path, cloud_settings, keypair, auth):
    import shutil

    shutil.copytree(REPO_ROOT / "catalog", tmp_path / "catalog")
    shutil.copytree(REPO_ROOT / "contracts", tmp_path / "contracts")
    stale = tmp_path / "catalog/.resolved/dev.json"
    stale.write_text(stale.read_text().replace('"cc-cx-br"', '"cc-tampered"'))
    response = make_client(cloud_settings(catalog_root=tmp_path), keypair).get("/api/catalog", headers=auth)
    assert response.status_code == 503
    assert response.json() == {"error": "catalog unavailable"}


def test_catalog_without_root_is_503(cloud_settings, keypair, auth):
    response = make_client(cloud_settings(catalog_root=None), keypair).get("/api/catalog", headers=auth)
    assert response.status_code == 503


# ---------------------------------------------------------------------------------------------
# no usage emission, no credential forwarding
# ---------------------------------------------------------------------------------------------


def test_bff_never_emits_usage_records(cloud_settings, keypair, auth, capsys, caplog):
    client = make_client(cloud_settings(demo=True), keypair)
    for path in ("/api/me", "/api/catalog", "/api/usage", "/api/audit", "/api/traces", f"/api/traces/{DEMO_TRACE_ID}"):
        client.get(path, headers=auth)
    out = capsys.readouterr()
    assert "esmeralda.usage" not in out.out + out.err
    assert "esmeralda.usage" not in caplog.text


def test_bff_source_does_not_import_usage_emitters_or_forward_credentials():
    src = API_DIR / "src" / "esmeralda_ui"
    for path in src.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in ("esmeralda.usage", "esmeralda.telemetry", "esmeralda.plugin"), path
                assert not (node.module == "esmeralda" and {a.name for a in node.names} & {"usage", "telemetry"}), path
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith(("esmeralda.usage", "esmeralda.telemetry")) for a in node.names), path
        text = path.read_text()
        assert "authorization" not in text.lower() or path.name == "security.py", path
