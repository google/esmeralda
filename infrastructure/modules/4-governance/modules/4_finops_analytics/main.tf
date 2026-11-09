# Unified Event Envelope Telemetry Table for Infinite Multi-Event Scaling
resource "google_bigquery_table" "unified_telemetry_events" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  table_id   = "genai_telemetry_events"
  project    = var.governance_project_id

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["event_type", "agent_id", "session_id"]

  schema = <<EOF
[
  {"name": "timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "event_type", "type": "STRING", "mode": "REQUIRED"},
  {"name": "session_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "user_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "agent_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "execution_path", "type": "STRING", "mode": "NULLABLE"},
  {"name": "payload", "type": "JSON", "mode": "NULLABLE"}
]
EOF
}

# Partitioned BigQuery Table for Cloud Audit Activity Logs
resource "google_bigquery_table" "cloudaudit_activity" {
  dataset_id          = "esmeralda_telemetry_logs_${var.environment}"
  table_id            = "cloudaudit_googleapis_com_activity"
  project             = var.governance_project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {
    "name": "protopayload_auditlog",
    "type": "RECORD",
    "mode": "NULLABLE",
    "fields": [
      {"name": "serviceName", "type": "STRING", "mode": "NULLABLE"},
      {"name": "methodName", "type": "STRING", "mode": "NULLABLE"},
      {"name": "resourceName", "type": "STRING", "mode": "NULLABLE"},
      {
        "name": "authenticationInfo",
        "type": "RECORD",
        "mode": "NULLABLE",
        "fields": [
          {"name": "principalEmail", "type": "STRING", "mode": "NULLABLE"}
        ]
      },
      {
        "name": "status",
        "type": "RECORD",
        "mode": "NULLABLE",
        "fields": [
          {"name": "message", "type": "STRING", "mode": "NULLABLE"}
        ]
      }
    ]
  }
]
EOF

  lifecycle {
    ignore_changes = [schema]
  }
}

# Partitioned BigQuery Table for Reasoning Engine stdout (the agents' legacy `genai_token_consumption`
# events, from before the esmeralda.usage contract; kept so historical data stays in the views).
# The central sinks would create it on the first agent log, but vw_usage_normalized queries it, so it
# is pre-created here with the columns it reads. The sinks add the remaining LogEntry columns on
# write; numeric jsonPayload values are exported as FLOAT.
resource "google_bigquery_table" "reasoning_engine_stdout" {
  dataset_id          = "esmeralda_telemetry_logs_${var.environment}"
  table_id            = "aiplatform_googleapis_com_reasoning_engine_stdout"
  project             = var.governance_project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "timestamp", "type": "TIMESTAMP", "mode": "NULLABLE"},
  {"name": "logName", "type": "STRING", "mode": "NULLABLE"},
  {"name": "severity", "type": "STRING", "mode": "NULLABLE"},
  {"name": "trace", "type": "STRING", "mode": "NULLABLE"},
  {
    "name": "jsonPayload",
    "type": "RECORD",
    "mode": "NULLABLE",
    "fields": [
      {"name": "event", "type": "STRING", "mode": "NULLABLE"},
      {"name": "agent_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "model", "type": "STRING", "mode": "NULLABLE"},
      {"name": "session_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "trace_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "user_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "execution_path", "type": "STRING", "mode": "NULLABLE"},
      {
        "name": "tokens",
        "type": "RECORD",
        "mode": "NULLABLE",
        "fields": [
          {"name": "prompt_tokens", "type": "FLOAT", "mode": "NULLABLE"},
          {"name": "completion_tokens", "type": "FLOAT", "mode": "NULLABLE"},
          {"name": "thoughts_tokens", "type": "FLOAT", "mode": "NULLABLE"},
          {"name": "cached_tokens", "type": "FLOAT", "mode": "NULLABLE"},
          {"name": "total_tokens", "type": "FLOAT", "mode": "NULLABLE"}
        ]
      }
    ]
  }
]
EOF

  # The sinks extend the schema as new log fields arrive.
  lifecycle {
    ignore_changes = [schema]
  }
}

# ------------------------------------------------------------------------------
# USAGE ANALYTICS (esmeralda.usage contract)
# ------------------------------------------------------------------------------
# Layering of the views (all in esmeralda_telemetry_logs_<env>):
#
#   esmeralda_usage_<env>.<landing tables>   (modules/1_telemetry_sinks; one per producer log ID)
#     -> usage_events                        flattened, typed esmeralda.usage records
#   genai_telemetry_events + reasoning_engine_stdout (legacy token events, historical data)
#     -> vw_usage_normalized                 native + legacy rows in one shape, plus attribution
#                                            (principal_teams lookup, verified flag, billed_team)
#     -> vw_usage_billable                   one row per (trace_id, agent): gateway and workload records
#                                            joined on trace_id, gateway tokens and identity preferred
#     -> vw_monthly_agent_chargeback, vw_request_level_telemetry (backwards-compatible columns)

# Principal -> team attribution lookup. Created empty here and filled by the catalog compiler
# (`esmeralda catalog resolve/compile`, see the observability design, phases 5-6) from the verified
# identities in the catalog. One row per authenticated principal:
#   principal     SPIFFE id, service account email, group, Apigee app name, or federated
#                 (issuer, subject) principal string, exactly as producers write caller.principal
#   team          owning team (catalog teams.yaml key); the team that pays for this caller's usage
#   cost_centers  cost centres this caller may declare (caller.declared.cost_center is accepted only
#                 if listed)
#   verified_by   how the identity is authenticated: spiffe | service_account | group | apigee_app |
#                 workload_identity_federation | oidc
# The compiler replaces the whole table on each compile, so rows are never edited by hand.
resource "google_bigquery_table" "principal_teams" {
  dataset_id          = "esmeralda_telemetry_logs_${var.environment}"
  table_id            = "principal_teams"
  project             = var.governance_project_id
  deletion_protection = false
  description         = "Verified principal -> team attribution, compiled from the catalog. Read by vw_usage_normalized."

  schema = jsonencode([
    { name = "principal", type = "STRING", mode = "REQUIRED", description = "Authenticated identity, as written in caller.principal" },
    { name = "team", type = "STRING", mode = "REQUIRED", description = "Catalog team billed for this principal's usage" },
    { name = "cost_centers", type = "STRING", mode = "REPEATED", description = "Cost centres this principal may declare" },
    { name = "verified_by", type = "STRING", mode = "NULLABLE", description = "spiffe | service_account | group | apigee_app | workload_identity_federation | oidc" },
  ])
}

# usage_events: every esmeralda.usage record, flattened to typed columns (one UNION ALL branch per
# landing table). The view is the stable name consumers query; the landing tables are an export detail.
resource "google_bigquery_table" "usage_events" {
  dataset_id  = "esmeralda_telemetry_logs_${var.environment}"
  table_id    = "usage_events"
  project     = var.governance_project_id
  description = "esmeralda.usage records (contracts/usage-event.schema.json) from every producer, flattened."

  view {
    query = templatefile("${path.module}/../../sql/usage_events.sql.tpl", {
      usage_dataset_id = var.usage_dataset_id
      landing_tables   = var.usage_landing_tables
    })
    use_legacy_sql = false
  }
}

# Native usage records plus the legacy token-event shapes, mapped into the same columns, with attribution.
resource "google_bigquery_table" "vw_usage_normalized" {
  dataset_id  = "esmeralda_telemetry_logs_${var.environment}"
  table_id    = "vw_usage_normalized"
  project     = var.governance_project_id
  description = "usage_events + legacy genai_telemetry_events / Reasoning Engine stdout token events in one shape, with verified attribution."

  view {
    query = templatefile("${path.module}/../../sql/vw_usage_normalized.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
    })
    use_legacy_sql = false
  }

  depends_on = [
    google_bigquery_table.usage_events,
    google_bigquery_table.principal_teams,
    google_bigquery_table.unified_telemetry_events,
    google_bigquery_table.reasoning_engine_stdout,
  ]
}

# One billable unit per (trace_id, agent): gateway and workload records joined, gateway preferred.
resource "google_bigquery_table" "vw_usage_billable" {
  dataset_id  = "esmeralda_telemetry_logs_${var.environment}"
  table_id    = "vw_usage_billable"
  project     = var.governance_project_id
  description = "Billable usage units: gateway and workload records joined on trace_id; gateway tokens and identity preferred."

  view {
    query = templatefile("${path.module}/../../sql/vw_usage_billable.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.vw_usage_normalized]
}

# Monthly Agent Chargeback View (billed by verified team; built on vw_usage_billable)
resource "google_bigquery_table" "vw_monthly_chargeback" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  table_id   = "vw_monthly_agent_chargeback"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_monthly_agent_chargeback.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.vw_usage_billable]
}

# Per-Request Level Telemetry View (one row per billable unit: a request trace for one agent)
resource "google_bigquery_table" "vw_request_level_telemetry" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  table_id   = "vw_request_level_telemetry"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_request_level_telemetry.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.vw_usage_billable]
}

# Security & Compliance Audit Trail View (IAM changes, secret accesses, deployments)
resource "google_bigquery_table" "vw_security_audit_trail" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  table_id   = "vw_security_audit_trail"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_security_audit_trail.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.cloudaudit_activity]
}

# The two views above used to be gated by enable_analytics_views (count = 0/1); keep their state.
moved {
  from = google_bigquery_table.vw_monthly_chargeback[0]
  to   = google_bigquery_table.vw_monthly_chargeback
}

moved {
  from = google_bigquery_table.vw_request_level_telemetry[0]
  to   = google_bigquery_table.vw_request_level_telemetry
}
