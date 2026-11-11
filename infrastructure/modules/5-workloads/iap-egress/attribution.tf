# infrastructure/modules/5-workloads/iap-egress/attribution.tf
# ====================================================================
# Verified attribution: the catalog's principal -> team rows and the gateway attribution count.
# --------------------------------------------------------------------
# This unit already maps every catalog `<unit>:<output>` reference to its value (identity_refs), so
# it also writes the catalog-compiled principal_teams rows (catalog/.resolved/<env>.json
# attribution.principal_teams) into the layer-4 principal_teams table, and counts Agent Gateway
# decisions that let through a principal without such a row (expected: 0).
#
# Table load: one BigQuery script job replaces the `source = 'catalog'` rows in a transaction, so
# rows from other writers (Terraform rows are served by vw_principal_teams from layer 4, other tools
# write their own source) are never touched, and the table schema is left as layer 4 defines it.
# BigQuery job ids are immutable and unique per project: the id carries a random suffix that only
# changes with the rows (random_id keepers), so a re-apply with unchanged rows is a no-op and every
# catalog change (including a revert to an earlier set) runs a new job.
# ====================================================================

locals {
  principal_rows = [
    for r in var.principal_teams_rows : {
      principal      = lookup(var.identity_refs, r.principal, r.principal)
      principal_type = r.principal_type
      team           = r.team
    }
  ]
  unresolved_principal_rows = [for r in local.principal_rows : r.principal if can(regex(local.ref_pattern, r.principal))]

  principal_teams_values = join(",\n  ", [
    for r in local.principal_rows : "(${jsonencode(r.principal)}, ${jsonencode(r.principal_type)}, ${jsonencode(r.team)}, 'catalog')"
  ])
  principal_teams_sql = join("\n", compact([
    "BEGIN TRANSACTION;",
    "DELETE FROM `${var.principal_teams_table}` WHERE source = 'catalog';",
    length(local.principal_rows) == 0 ? "" : join("\n", [
      "INSERT INTO `${var.principal_teams_table}` (principal, principal_type, team, source)",
      "SELECT * FROM UNNEST(ARRAY<STRUCT<principal STRING, principal_type STRING, team STRING, source STRING>>[",
      "  ${local.principal_teams_values}",
      "]);",
    ]),
    "COMMIT TRANSACTION;",
  ]))

  # Gateway decisions are keyed on the authenticated principal: Agent Identity principals
  # (principal://...) in principalSubject, service accounts in principalEmail.
  attributed_principals = distinct([for r in local.principal_rows : r.principal])
  attributed_subjects   = [for p in local.attributed_principals : p if startswith(p, "principal://")]
  attributed_emails     = [for p in local.attributed_principals : p if can(regex("^[^:/]+@[^:/]+$", p))]
  attributed_clauses = compact([
    length(local.attributed_subjects) > 0 ? "protoPayload.authenticationInfo.principalSubject=(${join(" OR ", [for s in local.attributed_subjects : jsonencode(s)])})" : "",
    length(local.attributed_emails) > 0 ? "protoPayload.authenticationInfo.principalEmail=(${join(" OR ", [for e in local.attributed_emails : jsonencode(e)])})" : "",
  ])
  gateway_unattributed_filter = join(" AND ", compact([
    "logName:\"cloudaudit.googleapis.com%2Fdata_access\"",
    "protoPayload.serviceName=\"iap.googleapis.com\"",
    "protoPayload.methodName=\"GatekeeperAuthorizer.AuthorizeUser\"",
    "protoPayload.resourceName=~\"/(agents|mcpServers)/\"",
    "protoPayload.authorizationInfo.granted=true",
    length(local.attributed_clauses) > 0 ? "NOT (${join(" OR ", local.attributed_clauses)})" : "",
  ]))
}

resource "random_id" "principal_teams_load" {
  count       = var.attribution_enabled ? 1 : 0
  byte_length = 4
  keepers = {
    sql = sha256(local.principal_teams_sql)
  }
}

resource "google_bigquery_job" "principal_teams_catalog" {
  count    = var.attribution_enabled ? 1 : 0
  project  = var.governance_project_id
  location = var.bigquery_location
  job_id   = "esmeralda_principal_teams_catalog_${substr(sha256(local.principal_teams_sql), 0, 12)}_${random_id.principal_teams_load[0].hex}"

  labels = {
    writer = "catalog"
  }

  query {
    query              = local.principal_teams_sql
    use_legacy_sql     = false
    create_disposition = ""
    write_disposition  = ""
  }

  lifecycle {
    precondition {
      condition     = var.principal_teams_table != ""
      error_message = "attribution_enabled needs principal_teams_table (<project>.<dataset>.principal_teams, layer 4 outputs)."
    }
    precondition {
      condition     = length(local.unresolved_principal_rows) == 0
      error_message = "principal_teams_rows references without a value in identity_refs: ${join(", ", local.unresolved_principal_rows)}. Map them to unit outputs in the iap-egress terragrunt unit."
    }
  }
}

# Agent Gateway decisions that granted a principal with no principal_teams row (expected: 0). The
# catalog fails closed, so every principal on a gateway list has a row; a count here means a grant
# that bypassed the catalog, or rows that weren't loaded.
resource "google_logging_metric" "gateway_unattributed_decisions" {
  count       = var.attribution_enabled ? 1 : 0
  project     = var.governance_project_id
  name        = "attribution/gateway_unattributed_decisions"
  filter      = local.gateway_unattributed_filter
  description = "Agent Gateway (IAP egress) decisions on agents and MCP servers that granted a principal without a catalog principal_teams row (expected: 0)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "target"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "target" = "REGEXP_EXTRACT(protoPayload.resourceName, \"/(?:agents|mcpServers)/([^/]+)$\")"
  }
}

resource "google_monitoring_alert_policy" "gateway_unattributed" {
  count        = var.attribution_enabled && var.alert_on_unattributed ? 1 : 0
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Gateway Attribution - Principal Without a Team"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 GATEWAY LET THROUGH A PRINCIPAL WITHOUT A TEAM
**Alert Trigger**: In the last 5 minutes the Agent Gateway granted a call to an agent or MCP server for a principal that has no row in the catalog's `principal_teams` (the `target` label names the registry entry). Its usage can't be billed to a verified team.

#### 🛠️ Immediate On-Call Action Plan:
1. Find the decisions in the governance project's Data Access audit logs with this filter:
   `${local.gateway_unattributed_filter}`
2. If the principal belongs in the catalog, add it (catalog `callers` or an agent target), run `esmeralda catalog resolve --env ${var.environment} --write` and re-apply this unit: the IAP grant and the principal_teams row come from the same entry.
3. Otherwise remove the grant: it bypassed the catalog.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Gateway decisions for principals without a principal_teams row"
    condition_threshold {
      filter                  = "metric.type = \"logging.googleapis.com/user/attribution/gateway_unattributed_decisions\""
      duration                = "0s"
      comparison              = "COMPARISON_GT"
      threshold_value         = 0
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["metric.label.target"]
      }
    }
  }

  notification_channels = var.notification_channels

  depends_on = [google_logging_metric.gateway_unattributed_decisions]
}
