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

"""Offline contract tests for Layer 4 usage analytics (esmeralda.usage sink, views, metrics, SLOs)."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GOVERNANCE = REPO_ROOT / "infrastructure/modules/4-governance"
SINKS_DIR = GOVERNANCE / "modules/1_telemetry_sinks"
ALERTS_DIR = GOVERNANCE / "modules/3_alert_policies"
FINOPS_TF = GOVERNANCE / "modules/4_finops_analytics/main.tf"
SQL_DIR = GOVERNANCE / "sql"

USAGE_FILTER = 'jsonPayload.schema=\\"esmeralda.usage\\"'  # as written inside an HCL string


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _blocks(text: str, kind: str, rtype: str) -> dict[str, str]:
    """Return {name: body} for every `<kind> "<rtype>" "<name>" { ... }` block (brace matched)."""
    out: dict[str, str] = {}
    for m in re.finditer(rf'^{kind} "{re.escape(rtype)}" "([^"]+)" \{{', text, re.M):
        depth, i = 0, m.end() - 1
        while True:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out[m.group(1)] = text[m.end() : i]
    return out


def _module_tf(directory: pathlib.Path) -> str:
    return "\n".join(_read(p) for p in sorted(directory.glob("*.tf")))


def _sql_without_comments(name: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in _read(SQL_DIR / name).splitlines())


def _final_select_columns(name: str) -> list[str]:
    """Column aliases of the outermost SELECT ... FROM of a view (good enough for our templates)."""
    sql = _sql_without_comments(name)
    last_select = sql.rindex("\nSELECT")
    body = sql[last_select : sql.index("\nFROM", last_select)]
    cols = []
    depth = 0
    current = ""
    for ch in body[len("\nSELECT") :]:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            cols.append(current)
            current = ""
        else:
            current += ch
    cols.append(current)
    names = []
    for col in cols:
        col = " ".join(col.split())
        m = re.search(r"\bAS (\w+)$", col)
        names.append(m.group(1) if m else col.split(".")[-1])
    return names


# ------------------------------------------------------------------------------
# Sink
# ------------------------------------------------------------------------------


def test_usage_sink_is_keyed_on_schema_never_on_log_name():
    sinks = _module_tf(SINKS_DIR)
    assert f'usage_sink_filter     = "{USAGE_FILTER}"' in sinks
    sink = _blocks(sinks, "resource", "google_logging_project_sink")["usage_events"]
    assert "filter = local.usage_sink_filter" in sink
    assert "logName" not in sink and "=~" not in sink
    assert "use_partitioned_tables = true" in sink
    assert "esmeralda_usage_" in _blocks(sinks, "resource", "google_bigquery_dataset")["usage_events"]
    # Landing tables exist before the sink can auto-create them.
    assert "depends_on = [google_bigquery_table.usage_landing]" in sink
    # Every project that can produce usage: governance (Agent Gateway) + spokes (agents, MCP, Apigee).
    assert "usage_source_projects = toset(concat([var.governance_project_id], var.spoke_project_ids))" in sinks


def test_usage_landing_tables_are_typed_and_day_partitioned():
    sinks = _module_tf(SINKS_DIR)
    landing = _blocks(sinks, "resource", "google_bigquery_table")["usage_landing"]
    assert 'type  = "DAY"' in landing and 'field = "timestamp"' in landing
    assert "schema = jsonencode(local.usage_landing_schema)" in landing
    schema = _read(SINKS_DIR / "usage_schema.tf")
    # Contract fields as Logging exports them (dots -> underscores, numbers -> FLOAT).
    for field in (
        '"schema"',
        '"schema_version", type = "FLOAT"',
        '"source"',
        '"trace_id"',
        '"verified", type = "BOOLEAN"',
        '"usage_input_tokens", type = "FLOAT"',
        '"usage_output_tokens", type = "FLOAT"',
        '"usage_cached_tokens", type = "FLOAT"',
        '"usage_reasoning_tokens", type = "FLOAT"',
        '"request_model"',
        '"dimensions"',
        '"product"',
    ):
        assert f"name = {field}" in schema, field


def test_central_and_audit_sinks_still_route_audit_logs_and_keep_usage_records():
    sinks = _read(SINKS_DIR / "main.tf")
    central = _blocks(sinks, "resource", "google_logging_project_sink")["central_sinks"]
    assert 'logName=~\\"cloudaudit.googleapis.com\\"' in central
    assert "NOT jsonPayload.schema=\\\"esmeralda.usage\\\"" in central  # never excluded as debug noise
    assert 'resource "google_bigquery_dataset_iam_member" "dataset_writers"' in sinks
    assert 'resource "google_storage_bucket" "telemetry_archive"' in sinks
    dlp = _read(GOVERNANCE / "modules/2_dlp_inspection/main.tf")
    assert "google_data_loss_prevention_inspect_template" in dlp


# ------------------------------------------------------------------------------
# Tables and views
# ------------------------------------------------------------------------------


def test_principal_teams_attribution_table_schema():
    finops = _read(FINOPS_TF)
    table = _blocks(finops, "resource", "google_bigquery_table")["principal_teams"]
    assert 'table_id            = "principal_teams"' in table
    assert "schema = jsonencode(local.principal_teams_columns)" in table
    # Base columns, in order, then the extension point.
    cols = re.findall(r'\{ name = "(\w+)", type = "STRING", mode = "(\w+)"', finops)
    assert cols[:4] == [("principal", "REQUIRED"), ("principal_type", "REQUIRED"), ("team", "REQUIRED"), ("source", "NULLABLE")]
    assert "], var.principal_teams_extra_columns)" in finops
    variables = _read(FINOPS_TF.parent / "variables.tf")
    for var in ("principal_teams", "principal_teams_extra_columns", "principal_type_values", "project_numbers"):
        assert f'variable "{var}"' in variables, var
    assert 'source         = optional(string, "terraform")' in variables
    assert '"gcp_project"' in variables


def test_principal_teams_view_unions_table_and_terraform_rows():
    view = _blocks(_read(FINOPS_TF), "resource", "google_bigquery_table")["vw_principal_teams"]
    assert "sql/vw_principal_teams.sql.tpl" in view
    assert "rows       = var.principal_teams" in view
    assert "contains(var.principal_type_values, r.principal_type)" in view
    sql = _read(SQL_DIR / "vw_principal_teams.sql.tpl")
    assert "FROM `${dataset_id}.principal_teams`" in sql
    assert "%{ for r in rows ~}" in sql and "jsonencode(r.principal)" in sql


def test_no_partner_view_without_its_sink():
    # The partner Apigee chargeback view ships with the partner sink (separate change).
    module = "\n".join(_read(p) for p in GOVERNANCE.rglob("*.tf"))
    assert "vw_partner_apigee_chargeback" not in module
    assert '"google_bigquery_table" "apigee_interactions"' not in module
    assert not (SQL_DIR / "vw_partner_apigee_chargeback.sql.tpl").exists()


def test_legacy_tables_are_kept_for_historical_data():
    tables = _blocks(_read(FINOPS_TF), "resource", "google_bigquery_table")
    for name in ("reasoning_engine_stdout", "unified_telemetry_events", "cloudaudit_activity", "vw_security_audit_trail"):
        assert name in tables, name


@pytest.mark.parametrize(
    ("view", "template", "must_reference", "must_not_reference"),
    [
        ("usage_events", "usage_events.sql.tpl", ["${usage_dataset_id}.${table}"], []),
        (
            "vw_usage_normalized",
            "vw_usage_normalized.sql.tpl",
            [
                "${dataset_id}.usage_events",
                "${dataset_id}.aiplatform_googleapis_com_reasoning_engine_stdout",
                "${dataset_id}.genai_telemetry_events",
                "${dataset_id}.vw_principal_teams",
            ],
            ["${dataset_id}.principal_teams`"],
        ),
        ("vw_usage_billable", "vw_usage_billable.sql.tpl", ["${dataset_id}.vw_usage_normalized"], ["reasoning_engine_stdout", "genai_telemetry_events"]),
        ("vw_monthly_chargeback", "vw_monthly_agent_chargeback.sql.tpl", ["${dataset_id}.vw_usage_billable"], ["reasoning_engine_stdout", "genai_telemetry_events"]),
        ("vw_request_level_telemetry", "vw_request_level_telemetry.sql.tpl", ["${dataset_id}.vw_usage_billable"], ["reasoning_engine_stdout", "genai_telemetry_events"]),
    ],
)
def test_view_references(view, template, must_reference, must_not_reference):
    resource = _blocks(_read(FINOPS_TF), "resource", "google_bigquery_table")[view]
    assert f"sql/{template}" in resource
    sql = _sql_without_comments(template)
    for ref in must_reference:
        assert f"`{ref}`" in sql, ref
    for ref in must_not_reference:
        assert ref not in sql, ref


def test_usage_events_unions_every_landing_table():
    sql = _read(SQL_DIR / "usage_events.sql.tpl")
    assert "%{ for i, table in landing_tables ~}" in sql
    assert "UNION ALL" in sql
    assert "WHERE jsonPayload.schema = 'esmeralda.usage'" in sql
    # Logging's export names for the contract's dotted keys.
    for col in ("gen_ai.usage_input_tokens", "gen_ai.usage_output_tokens", "gen_ai.request_model", "caller.verified", "gateway.product"):
        assert f"jsonPayload.{col}" in sql, col


def test_billing_never_sums_agent_invocation_rollups():
    sql = _sql_without_comments("vw_usage_billable.sql.tpl")
    assert "WHERE event IN ('llm.call', 'gateway.request')" in sql
    assert "agent.invocation" not in sql
    # Gateway tokens first, then workload, then legacy.
    assert sql.index("WHEN gw.total_tokens > 0 THEN 'gateway'") < sql.index("WHEN wl.total_tokens > 0 THEN 'workload'")
    # Joined on trace_id + agent.
    assert "CONCAT(trace_id, '|', COALESCE(agent_key, ''))" in sql


def test_attribution_trusts_only_verified_callers():
    sql = _sql_without_comments("vw_usage_normalized.sql.tpl")
    order = [
        "THEN 'legacy'",
        "WHEN r.caller_verified AND r.caller_team IS NOT NULL THEN 'verified'",
        "WHEN r.caller_verified AND cpt.team IS NOT NULL THEN 'principal_table'",
        "WHEN r.source = 'workload' AND wpt.team IS NOT NULL THEN 'workload'",
        "THEN 'declared'",
        "ELSE 'unattributed'",
    ]
    positions = [sql.index(o) for o in order]
    assert positions == sorted(positions)
    assert "ON r.caller_verified AND cpt.principal = r.caller_principal" in sql
    # The workload floor keys on the log project (set by Cloud Logging), never on a payload field.
    assert "ON wpt.project_id = r.workload_project" in sql
    assert "WHEN r.source = 'workload' THEN wpt.team" in sql
    assert "COALESCE(billing_team, 'unattributed') AS billed_team" in sql
    # Declared projects only ever feed declared_team.
    assert "COALESCE(r.caller_team, dpt.team) AS declared_team" in sql
    billable = _sql_without_comments("vw_usage_billable.sql.tpl")
    assert billable.index("WHEN 'principal_table' THEN 0") < billable.index("WHEN 'workload' THEN 1") < billable.index("WHEN 'declared' THEN 2")


def test_project_numbers_are_mapped_to_ids():
    sql = _sql_without_comments("vw_usage_normalized.sql.tpl")
    assert "JSON_VALUE(TO_JSON(jsonPayload.caller.declared), '$.project_id') AS declared_project" in _read(SQL_DIR / "usage_events.sql.tpl")
    assert "LEFT JOIN project_numbers AS cpn ON cpn.project_number = r.caller_project_number" in sql
    assert "COALESCE(cpn.project_id, r.caller_project_number)" in sql
    main = _read(GOVERNANCE / "main.tf")
    assert 'data "google_project" "usage_sources"' in main
    assert "project_numbers               = { for p in data.google_project.usage_sources : p.number => p.project_id }" in main


def test_token_columns_are_int64():
    sql = _sql_without_comments("vw_usage_normalized.sql.tpl")
    # JSON payload numbers can be doubles ("336.0"): CAST(JSON_VALUE(...) AS INT64) would fail.
    assert not re.search(r"CAST\(JSON_VALUE\([^)]*tokens[^)]*\) AS INT64\)", sql)
    for col in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "total_tokens"):
        assert f"CAST({col} AS INT64) AS {col}" in sql or f"AS INT64) AS {col}" in sql, col
        assert f"LAX_INT64(payload.tokens." in sql
    events = _read(SQL_DIR / "usage_events.sql.tpl")
    for key in ("input", "output", "cached", "reasoning"):
        assert f"SAFE_CAST(jsonPayload.gen_ai.usage_{key}_tokens AS INT64) AS {key}_tokens" in events, key


def test_chargeback_columns_are_backwards_compatible():
    cols = _final_select_columns("vw_monthly_agent_chargeback.sql.tpl")
    legacy = [
        "billing_month", "agent_id", "model", "total_requests", "total_tokens", "uncached_prompt_tokens",
        "cached_prompt_tokens", "cache_hit_ratio_pct", "est_uncached_prompt_cost_usd", "est_cached_prompt_cost_usd",
        "est_response_cost_usd", "est_reasoning_cost_usd", "net_total_chargeback_usd",
    ]
    assert cols[: len(legacy)] == legacy
    assert {"billed_team", "verified", "attribution"} <= set(cols)


def test_request_level_columns_are_backwards_compatible():
    cols = _final_select_columns("vw_request_level_telemetry.sql.tpl")
    legacy = [
        "timestamp", "session_id", "trace_id", "user_id", "agent_id", "execution_path", "model", "prompt_tokens",
        "completion_tokens", "thoughts_tokens", "cached_tokens", "total_tokens", "request_cost_usd",
    ]
    assert cols[: len(legacy)] == legacy
    assert {"billed_team", "verified", "token_source"} <= set(cols)


def test_dev_attribution_rows_come_from_project_outputs():
    hcl = _read(REPO_ROOT / "infrastructure/live/dev/layer-4-governance/terragrunt.hcl")
    block = hcl[hcl.index("principal_teams = [") : hcl.index("]", hcl.index("principal_teams = ["))]
    # No literal project ids: every principal is a layer-1 output.
    principals = re.findall(r"principal = ([^,]+),", block)
    assert principals and all(p.startswith("dependency.projects.outputs.") for p in principals)


def _tf_binary() -> str | None:
    if os.environ.get("TF_BIN") and shutil.which(os.environ["TF_BIN"]):
        return shutil.which(os.environ["TF_BIN"])
    user_tf = pathlib.Path.home() / ".terraform/bin/terraform"
    for candidate in (os.fspath(user_tf), "terraform", "tofu"):
        if shutil.which(candidate):
            return shutil.which(candidate)
    return None


def test_usage_events_template_renders_one_branch_per_landing_table(tmp_path):
    tf = _tf_binary()
    if tf is None:
        pytest.skip("terraform not available")
    expr = (
        f'templatefile("{SQL_DIR / "usage_events.sql.tpl"}", '
        '{usage_dataset_id = "esmeralda_usage_dev", landing_tables = ["table_a", "table_b", "table_c"]})'
    )
    res = subprocess.run([tf, f"-chdir={tmp_path}", "console"], input=expr, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    rendered = res.stdout
    assert rendered.count("UNION ALL") == 2
    for t in ("table_a", "table_b", "table_c"):
        assert f"`esmeralda_usage_dev.{t}`" in rendered
    assert "%{" not in rendered and "${" not in rendered


def _render(tmp_path, template: str, variables: str) -> str:
    tf = _tf_binary()
    if tf is None:
        pytest.skip("terraform not available")
    expr = f'templatefile("{SQL_DIR / template}", {variables})'
    res = subprocess.run([tf, f"-chdir={tmp_path}", "console"], input=expr, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    assert "%{" not in res.stdout and "${" not in res.stdout
    return res.stdout


def test_normalized_template_renders_project_numbers(tmp_path):
    rendered = _render(
        tmp_path,
        "vw_usage_normalized.sql.tpl",
        '{dataset_id = "ds", project_numbers = {"111" = "example-a", "222" = "example-b"}}',
    )
    assert 'STRUCT("111" AS project_number, "example-a" AS project_id),' in rendered
    assert 'STRUCT("222" AS project_number, "example-b" AS project_id)\n' in rendered
    empty = _render(tmp_path, "vw_usage_normalized.sql.tpl", '{dataset_id = "ds", project_numbers = {}}')
    assert "LIMIT 0" in empty and "UNNEST" not in empty


def test_principal_teams_template_renders_terraform_rows(tmp_path):
    rendered = _render(
        tmp_path,
        "vw_principal_teams.sql.tpl",
        '{dataset_id = "ds", rows = [{principal = "example-a", principal_type = "gcp_project", team = "cx", source = "terraform"}]}',
    )
    assert "FROM `ds.principal_teams`" in rendered
    assert 'SELECT "example-a", "gcp_project", "cx", "terraform"' in rendered
    assert rendered.count("UNION ALL") == 1


# ------------------------------------------------------------------------------
# Metrics, alerts, dashboards, SLOs
# ------------------------------------------------------------------------------


def test_no_regex_or_stdout_only_finops_metric_remains():
    alerts = _module_tf(ALERTS_DIR)
    metrics = _blocks(alerts, "resource", "google_logging_metric")
    # The pre-contract metrics keyed on stdout events are gone.
    assert "genai/" not in alerts
    for legacy in ("genai_token_consumption", "mcp_tool_execution", "reasoning_engine_stdout"):
        assert legacy not in alerts, legacy
    usage_metrics = {n: b for n, b in metrics.items() if n.startswith("usage_")}
    assert set(usage_metrics) == {"usage_tokens", "usage_tool_calls", "usage_unattributed_records"}
    assert f'usage_record_filter   = "{USAGE_FILTER}"' in alerts
    for name, body in usage_metrics.items():
        filter_line = next(line for line in body.splitlines() if line.strip().startswith("filter"))
        assert "local.usage_record_filter" in filter_line, name
        assert "=~" not in body and "textPayload" not in body and "logName" not in body, name
    # Only security (audit log) and ops (Envoy) metrics remain besides the usage ones.
    assert set(metrics) - set(usage_metrics) == {
        "gateway_token_fetch_failures",
        "iam_privilege_changes",
        "secret_access_operations",
        "reasoning_engine_deployments",
    }


def test_token_metrics_cover_team_agent_model_and_tools_by_status():
    alerts = _module_tf(ALERTS_DIR)
    for key in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"):
        assert f"    {key} = {{" in alerts, key
    for label in ("caller_team", "verified", "workload", "workload_team", "model"):
        assert re.search(rf"^\s+{label}\s+= \"EXTRACT\(", alerts, re.M), label
    tools = _blocks(alerts, "resource", "google_logging_metric")["usage_tool_calls"]
    assert 'jsonPayload.event=\\"tool.call\\"' in tools
    for label in ('"tool"', '"server"', '"status"'):
        assert label in tools


def test_alerts_run_on_usage_metrics():
    alerts = _module_tf(ALERTS_DIR)
    policies = _blocks(alerts, "resource", "google_monitoring_alert_policy")
    assert "logging.googleapis.com/user/usage/input_tokens" in policies["runaway_loop_token_cap"]
    assert "logging_googleapis_com:user_usage_input_tokens_sum" in policies["token_anomaly_spike"]
    unattributed = policies["unattributed_usage"]
    assert "logging.googleapis.com/user/usage/unattributed_records" in unattributed
    assert "threshold_value         = 0" in unattributed and '"COMPARISON_GT"' in unattributed
    unattributed_metric = _blocks(alerts, "resource", "google_logging_metric")["usage_unattributed_records"]
    assert "NOT jsonPayload.caller.verified=true" in unattributed_metric
    # Security alerting is kept.
    assert "privilege_escalation_alert" in policies


def test_dashboards_are_valid_json_and_read_usage_metrics():
    text = _read(ALERTS_DIR / "dashboards.tf")
    bodies = re.findall(r"dashboard_json = <<EOF\n(.*?)\nEOF", text, re.S)
    assert len(bodies) == 2
    for body in bodies:
        json.loads(body.replace("${var.environment}", "dev"))
    assert "timeSeriesQueryLanguage" not in text  # no MQL on removed metrics
    assert "user_usage_input_tokens_sum" in bodies[1]
    assert "user_usage_tool_calls" in bodies[1]
    assert "gen_ai.invoke_agent.duration_bucket" in bodies[0]  # native ADK OTel ops tiles


def test_slos_target_real_services():
    slo = "\n".join(line.split("#", 1)[0] for line in _read(ALERTS_DIR / "slo.tf").splitlines())
    # The orchestrator runs on Agent Engine: no CLOUD_RUN basic service pointing at it.
    assert "CLOUD_RUN" not in slo and "basic_service" not in slo
    assert "cx-mortgage-orchestrator" not in slo
    services = _blocks(slo, "resource", "google_monitoring_custom_service")
    assert set(services) == {"mcp", "agents"}
    slos = _blocks(slo, "resource", "google_monitoring_slo")
    assert set(slos) == {"mcp_availability", "mcp_latency", "agent_availability", "agent_latency"}
    assert "run.googleapis.com/request_latencies" in slos["mcp_latency"]
    assert "aiplatform.googleapis.com/reasoning_engine/request_latencies" in slos["agent_latency"]
