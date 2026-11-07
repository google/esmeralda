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

# Partitioned BigQuery Table for Reasoning Engine stdout (the agents' structured token events).
# The central sinks would create it on the first agent log, but the chargeback and per-request
# views query it, so it is pre-created here with the columns they read. The sinks add the
# remaining LogEntry columns on write; numeric jsonPayload values are exported as FLOAT.
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
      {"name": "consumer_client_id", "type": "STRING", "mode": "NULLABLE"},
      {"name": "caller_identity", "type": "STRING", "mode": "NULLABLE"},
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

# Monthly Agent Chargeback View
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

  depends_on = [
    google_bigquery_table.unified_telemetry_events,
    google_bigquery_table.reasoning_engine_stdout,
  ]
}

# Per-Request Level Telemetry View (Lists every individual request & session)
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

  depends_on = [
    google_bigquery_table.unified_telemetry_events,
    google_bigquery_table.reasoning_engine_stdout,
  ]
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
