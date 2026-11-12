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

"""Offline contract tests for verified attribution in layers 4 and 5 (unattributed checks, principal_teams load)."""

from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GOVERNANCE = REPO_ROOT / "infrastructure/modules/4-governance"
ALERTS_TF = GOVERNANCE / "modules/3_alert_policies/main.tf"
IAP_EGRESS = REPO_ROOT / "infrastructure/modules/5-workloads/iap-egress"
PRINCIPAL_TYPES = {"gcp_project", "agent_runtime", "spiffe", "service_account", "group", "apigee_app",
                   "workload_identity_federation", "oidc"}


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(text: str, kind: str, rtype: str, name: str) -> str:
    m = re.search(rf'^{kind} "{re.escape(rtype)}" "{re.escape(name)}" \{{', text, re.M)
    assert m, f"{kind} {rtype}.{name} not found"
    depth, i = 0, m.end() - 1
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[m.end() : i]
        i += 1


def test_unattributed_checks_count_guarded_workloads_only():
    """Agent Runtime agents never see the caller's credential: their records are gateway-attributed.

    usage/unattributed_records stays as deployed (every unverified record); the alert and the dashboard
    tile read usage/unattributed_guarded_records, a separate metric scoped in its log filter, through
    the usage_promql generator (exact monitored_resource selectors). Separate, so no deployed metric is
    replaced.
    """
    alerts = _read(ALERTS_TF)
    metric = _block(alerts, "resource", "google_logging_metric", "usage_unattributed_records")
    assert "NOT jsonPayload.caller.verified=true" in metric and "runtime" not in metric
    guarded = _block(alerts, "resource", "google_logging_metric", "usage_unattributed_guarded_records")
    assert 'name        = "usage/unattributed_guarded_records"' in guarded
    assert 'NOT jsonPayload.caller.verified=true AND NOT jsonPayload.workload.runtime=\\"agent_engine\\"' in guarded
    assert '"increase|unattributed_guarded_records|5m"' in alerts
    alert = _block(alerts, "resource", "google_monitoring_alert_policy", "unattributed_usage")
    assert 'local.usage_promql["increase|unattributed_guarded_records|5m"]' in alert
    assert "google_logging_metric.usage_unattributed_guarded_records" in alert
    dashboards = _read(GOVERNANCE / "modules/3_alert_policies/dashboards.tf")
    assert 'usage_promql_json[\\"increase|unattributed_guarded_records|5m\\"]' in dashboards \
        or 'usage_promql_json["increase|unattributed_guarded_records|5m"]' in dashboards


def test_unattributed_alert_is_on_by_default():
    for path in (GOVERNANCE / "variables.tf", GOVERNANCE / "modules/3_alert_policies/variables.tf"):
        text = _read(path)
        start = text.index('variable "alert_on_unattributed_usage"')
        assert re.search(r"default\s*=\s*true", text[start : text.index("}", start)]), path
    outputs = _read(GOVERNANCE / "outputs.tf")
    assert 'output "alert_notification_channels"' in outputs and 'output "alert_on_unattributed_usage"' in outputs


def test_principal_teams_load_replaces_only_catalog_rows():
    tf = _read(IAP_EGRESS / "attribution.tf")
    assert "DELETE FROM `${var.principal_teams_table}` WHERE source = 'catalog';" in tf
    assert "BEGIN TRANSACTION;" in tf and "COMMIT TRANSACTION;" in tf
    job = _block(tf, "resource", "google_bigquery_job", "principal_teams_catalog")
    # DML script: never replaces the table (schema stays as layer 4 defines it).
    assert 'create_disposition = ""' in job and 'write_disposition  = ""' in job
    # Job ids are immutable: unchanged rows keep the id (no-op), changed rows get a fresh one.
    assert "sha256(local.principal_teams_sql)" in job and "random_id.principal_teams_load[0].hex" in job
    keepers = _block(tf, "resource", "random_id", "principal_teams_load")
    assert "sql = sha256(local.principal_teams_sql)" in keepers
    assert "local.unresolved_principal_rows" in job


def test_gateway_count_excludes_every_attributed_principal():
    tf = _read(IAP_EGRESS / "attribution.tf")
    metric = _block(tf, "resource", "google_logging_metric", "gateway_unattributed_decisions")
    assert 'name        = "attribution/gateway_unattributed_decisions"' in metric
    assert "local.gateway_unattributed_filter" in metric
    for fragment in ("GatekeeperAuthorizer.AuthorizeUser", "authorizationInfo.granted=true",
                     "authenticationInfo.principalSubject", "authenticationInfo.principalEmail"):
        assert fragment in tf, fragment
    alert = _block(tf, "resource", "google_monitoring_alert_policy", "gateway_unattributed")
    assert "var.attribution_enabled && var.alert_on_unattributed" in alert
    assert "threshold_value = 0" in alert
    # Cloud Monitoring rejects evaluation_missing_data with a zero duration.
    assert "evaluation_missing_data =" not in alert


def test_iap_egress_units_wire_the_attribution():
    for env, enabled in (("dev", "true"), ("prd", "false")):
        unit = _read(REPO_ROOT / f"infrastructure/live/{env}/layer-5-workloads/iap-egress/terragrunt.hcl")
        for fragment in ("principal_teams_rows", "principal_teams_table", "alert_notification_channels",
                         "alert_on_unattributed_usage", "layer-5-workloads/apigee/platform:proxy_runtime_sa_email"):
            assert fragment in unit, (env, fragment)
        env_yaml = _read(REPO_ROOT / f"infrastructure/live/{env}/env.yaml")
        assert re.search(rf"principal_teams\s*=\s*{enabled}\b", env_yaml), env


def test_catalog_rows_use_the_table_principal_types():
    for env in ("dev", "prd"):
        rows = json.loads(_read(REPO_ROOT / f"catalog/.resolved/{env}.json"))["attribution"]["principal_teams"]
        assert rows, env
        assert {r["principal_type"] for r in rows} <= PRINCIPAL_TYPES, env
        assert all(r["team"] for r in rows), env
