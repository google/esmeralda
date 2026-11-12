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
  {"name": "insertId", "type": "STRING", "mode": "NULLABLE"},
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
#   principal_teams (table) + var.principal_teams (Terraform rows)
#     -> vw_principal_teams                  effective principal -> team mapping
#     -> vw_usage_normalized                 native + legacy rows in one shape, plus attribution
#                                            (vw_principal_teams lookup, verified flag, billed_team)
#     -> vw_usage_billable                   one row per (trace_id, agent): gateway and workload records
#                                            joined on trace_id, gateway tokens and identity preferred
#     -> vw_monthly_agent_chargeback, vw_request_level_telemetry (backwards-compatible columns)

# Principal -> team attribution lookup: one row per authenticated identity.
#   principal       the identity, exactly as the attribution reads it: caller.principal (SPIFFE id,
#                   service account email, Apigee app, federated principal...) or, for
#                   principal_type = gcp_project, a project ID (the project a usage record was logged
#                   in, or the project a caller declares)
#   principal_type  what kind of identity `principal` is (var.principal_type_values)
#   team            owning team (catalog teams.yaml key): the team that pays for this identity's usage
#   source          who wrote the row: terraform (var.principal_teams), catalog (the catalog
#                   compiler), or another tool
# Columns beyond these come from var.principal_teams_extra_columns (NULLABLE or REPEATED only, so
# existing rows stay valid and BigQuery adds them in place). Rows reach the attribution through
# vw_principal_teams, which unions this table (written by tools such as the catalog compiler) with
# the rows declared in Terraform (var.principal_teams).
locals {
  principal_teams_columns = concat([
    { name = "principal", type = "STRING", mode = "REQUIRED", description = "Authenticated identity: caller.principal, or a project ID for principal_type = gcp_project" },
    { name = "principal_type", type = "STRING", mode = "REQUIRED", description = join(" | ", var.principal_type_values) },
    { name = "team", type = "STRING", mode = "REQUIRED", description = "Catalog team billed for this principal's usage" },
    { name = "source", type = "STRING", mode = "NULLABLE", description = "Who wrote the row: terraform | catalog | <tool>" },
  ], var.principal_teams_extra_columns)
}

resource "google_bigquery_table" "principal_teams" {
  dataset_id          = "esmeralda_telemetry_logs_${var.environment}"
  table_id            = "principal_teams"
  project             = var.governance_project_id
  deletion_protection = false
  description         = "Principal -> team attribution written by tools (catalog compiler). Read through vw_principal_teams."

  schema = jsonencode(local.principal_teams_columns)
}

# The effective principal -> team mapping: the principal_teams table plus the Terraform-declared rows.
resource "google_bigquery_table" "vw_principal_teams" {
  dataset_id  = "esmeralda_telemetry_logs_${var.environment}"
  table_id    = "vw_principal_teams"
  project     = var.governance_project_id
  description = "Effective principal -> team mapping: principal_teams table + rows declared in Terraform (source = terraform)."

  view {
    query = templatefile("${path.module}/../../sql/vw_principal_teams.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
      rows       = var.principal_teams
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.principal_teams]

  lifecycle {
    precondition {
      condition     = alltrue([for r in var.principal_teams : contains(var.principal_type_values, r.principal_type)])
      error_message = "principal_teams has a principal_type outside principal_type_values (${join(", ", var.principal_type_values)})."
    }
  }
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
      dataset_id      = "esmeralda_telemetry_logs_${var.environment}"
      project_numbers = var.project_numbers
    })
    use_legacy_sql = false
  }

  depends_on = [
    google_bigquery_table.usage_events,
    google_bigquery_table.vw_principal_teams,
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

# Partitioned BigQuery Table for Apigee Partner Gateway interactions (when usage_metering = "apigee").
# Pre-created with the OTel GenAI columns emitted by ML-interaction.xml so vw_partner_apigee_chargeback
# can be validated and queried immediately before the first partner call arrives.
resource "google_bigquery_table" "apigee_interactions" {
  dataset_id          = "esmeralda_telemetry_logs_${var.environment}"
  table_id            = "esmeralda_apigee_interactions"
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
      {"name": "proxy", "type": "STRING", "mode": "NULLABLE"},
      {"name": "revision", "type": "STRING", "mode": "NULLABLE"},
      {"name": "verb", "type": "STRING", "mode": "NULLABLE"},
      {"name": "path", "type": "STRING", "mode": "NULLABLE"},
      {"name": "status", "type": "STRING", "mode": "NULLABLE"},
      {"name": "fault", "type": "STRING", "mode": "NULLABLE"},
      {"name": "traceparent", "type": "STRING", "mode": "NULLABLE"},
      {"name": "apigee_message_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "consumer_app", "type": "STRING", "mode": "NULLABLE"},
      {"name": "caller_identity", "type": "STRING", "mode": "NULLABLE"},
      {"name": "caller_issuer", "type": "STRING", "mode": "NULLABLE"},
      {"name": "gen_ai_request_model", "type": "STRING", "mode": "NULLABLE"},
      {"name": "gen_ai_usage_input_tokens", "type": "STRING", "mode": "NULLABLE"},
      {"name": "gen_ai_usage_output_tokens", "type": "STRING", "mode": "NULLABLE"},
      {"name": "gen_ai_usage_thinking_tokens", "type": "STRING", "mode": "NULLABLE"},
      {"name": "gen_ai_usage_total_tokens", "type": "STRING", "mode": "NULLABLE"},
      {
        "name": "quota",
        "type": "RECORD",
        "mode": "NULLABLE",
        "fields": [
          {"name": "allowed", "type": "STRING", "mode": "NULLABLE"},
          {"name": "used", "type": "STRING", "mode": "NULLABLE"},
          {"name": "available", "type": "STRING", "mode": "NULLABLE"},
          {"name": "exceeded", "type": "STRING", "mode": "NULLABLE"}
        ]
      },
      {
        "name": "latency_timestamps",
        "type": "RECORD",
        "mode": "NULLABLE",
        "fields": [
          {"name": "client_received_start_ms", "type": "STRING", "mode": "NULLABLE"},
          {"name": "target_sent_start_ms", "type": "STRING", "mode": "NULLABLE"},
          {"name": "target_received_end_ms", "type": "STRING", "mode": "NULLABLE"},
          {"name": "gateway_responded_ms", "type": "STRING", "mode": "NULLABLE"}
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

# Partner Apigee Gateway Chargeback & Latency Decomposition View
resource "google_bigquery_table" "vw_partner_apigee_chargeback" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  table_id   = "vw_partner_apigee_chargeback"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_partner_apigee_chargeback.sql.tpl", {
      dataset_id = "esmeralda_telemetry_logs_${var.environment}"
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.apigee_interactions]
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
