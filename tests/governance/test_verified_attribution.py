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
import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GOVERNANCE = REPO_ROOT / "infrastructure/modules/4-governance"
ALERTS_TF = GOVERNANCE / "modules/3_alert_policies/main.tf"
IAP_EGRESS = REPO_ROOT / "infrastructure/modules/5-workloads/iap-egress"
PRINCIPAL_TYPES = {"gcp_project", "agent_runtime", "spiffe", "service_account", "group", "apigee_app",
                   "workload_identity_federation", "oidc"}


def _tf_binary() -> str:
    if os.environ.get("TF_BIN") and shutil.which(os.environ["TF_BIN"]):
        return shutil.which(os.environ["TF_BIN"])  # type: ignore[return-value]
    user_tf = pathlib.Path.home() / ".terraform/bin/terraform"
    for candidate in (os.fspath(user_tf), "terraform", "tofu"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("Neither terraform nor tofu binary is available on this machine.")


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


def test_layer4_defines_catalog_targets_table_and_view():
    finops_tf = _read(GOVERNANCE / "modules/4_finops_analytics/main.tf")
    table = _block(finops_tf, "resource", "google_bigquery_table", "catalog_targets")
    assert 'table_id            = "catalog_targets"' in table
    assert "schema = jsonencode(local.catalog_targets_columns)" in table
    for col in ("target", "kind", "owner_team", "unit", "endpoint", "token", "probe", "callers_json", "source"):
        assert f'name = "{col}"' in finops_tf, col
    view = _block(finops_tf, "resource", "google_bigquery_table", "vw_catalog_targets")
    assert "sql/vw_catalog_targets.sql.tpl" in view
    sql = _read(GOVERNANCE / "sql/vw_catalog_targets.sql.tpl")
    assert "FROM `${dataset_id}.catalog_targets`" in sql
    for out_path in (GOVERNANCE / "modules/4_finops_analytics/outputs.tf", GOVERNANCE / "outputs.tf"):
        outputs = _read(out_path)
        assert 'output "catalog_targets_table_id"' in outputs
        assert 'output "catalog_targets_view_id"' in outputs


def test_runtime_catalog_store_secret_and_bigquery_load():
    tf = _read(IAP_EGRESS / "runtime_catalog.tf")
    secret = _block(tf, "resource", "google_secret_manager_secret", "runtime_catalog")
    assert 'secret_id = "esmeralda-runtime-catalog-${var.environment}"' in secret
    assert "auto {}" in secret
    version = _block(tf, "resource", "google_secret_manager_secret_version", "runtime_catalog")
    assert "secret_data = jsonencode(local.runtime_catalog_payload)" in version

    assert 'schema         = "esmeralda.runtime_catalog"' in tf
    assert "DELETE FROM `${var.catalog_targets_table}` WHERE source = 'catalog';" in tf
    assert "INSERT INTO `${var.catalog_targets_table}` (target, kind, owner_team, unit, endpoint, token, probe, callers_json, source)" in tf
    job = _block(tf, "resource", "google_bigquery_job", "catalog_targets_catalog")
    assert 'create_disposition = ""' in job and 'write_disposition  = ""' in job
    assert "sha256(local.catalog_targets_sql)" in job and "random_id.catalog_targets_load[0].hex" in job
    keepers = _block(tf, "resource", "random_id", "catalog_targets_load")
    assert "sql = sha256(local.catalog_targets_sql)" in keepers

    variables = _read(IAP_EGRESS / "variables.tf")
    for var in ("runtime_catalog_enabled", "catalog_resolved", "governance_capabilities", "catalog_targets_table"):
        assert f'variable "{var}"' in variables, var
    outputs = _read(IAP_EGRESS / "outputs.tf")
    for out in ("runtime_catalog_secret_id", "runtime_catalog_secret_name", "runtime_catalog_payload"):
        assert f'output "{out}"' in outputs, out

    for env in ("dev", "prd"):
        unit = _read(REPO_ROOT / f"infrastructure/live/{env}/layer-5-workloads/iap-egress/terragrunt.hcl")
        for fragment in ("catalog_resolved", "governance_capabilities", "catalog_targets_table", "catalog_targets_table_id"):
            assert fragment in unit, (env, fragment)


def _init_and_validate(module_dir: pathlib.Path) -> None:
    tf = _tf_binary()
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    init_args = [tf, f"-chdir={module_dir}", "init", "-backend=false", "-input=false", "-no-color"]
    providers_dir = module_dir / ".terraform/providers"
    plugin_cache = pathlib.Path.home() / ".terraform.d/plugin-cache"
    if providers_dir.is_dir():
        init_args.append(f"-plugin-dir={providers_dir}")
    elif plugin_cache.is_dir():
        init_args.append(f"-plugin-dir={plugin_cache}")
    init_res = subprocess.run(init_args, capture_output=True, text=True, env=env)
    assert init_res.returncode == 0, init_res.stdout + init_res.stderr

    val_res = subprocess.run(
        [tf, f"-chdir={module_dir}", "validate", "-no-color"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert val_res.returncode == 0, val_res.stdout + val_res.stderr


@pytest.mark.parametrize(
    "module_dir",
    [GOVERNANCE, IAP_EGRESS],
    ids=["4-governance", "5-workloads/iap-egress"],
)
def test_terraform_validate_runtime_catalog_modules(module_dir: pathlib.Path) -> None:
    """Both Layer 4 governance and Layer 5 iap-egress pass offline `terraform init -backend=false` + `terraform validate`."""
    _init_and_validate(module_dir)



