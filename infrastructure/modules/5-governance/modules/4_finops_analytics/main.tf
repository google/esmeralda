# Reference Central BigQuery Telemetry Dataset
data "google_bigquery_dataset" "telemetry_logs" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  project    = var.governance_project_id
}

# Partitioned BigQuery Table for Structured Token Events
resource "google_bigquery_table" "token_events" {
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
  table_id   = "genai_token_events"
  project    = var.governance_project_id

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["agent_id", "user_id", "model"]

  schema = <<EOF
[
  {"name": "timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "agent_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "execution_path", "type": "STRING", "mode": "NULLABLE"},
  {"name": "session_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "user_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "model", "type": "STRING", "mode": "NULLABLE"},
  {
    "name": "tokens",
    "type": "RECORD",
    "mode": "NULLABLE",
    "fields": [
      {"name": "prompt_tokens", "type": "INTEGER", "mode": "NULLABLE"},
      {"name": "completion_tokens", "type": "INTEGER", "mode": "NULLABLE"},
      {"name": "thoughts_tokens", "type": "INTEGER", "mode": "NULLABLE"},
      {"name": "cached_tokens", "type": "INTEGER", "mode": "NULLABLE"},
      {"name": "total_tokens", "type": "INTEGER", "mode": "NULLABLE"}
    ]
  }
]
EOF
}

# Monthly Agent Chargeback View
resource "google_bigquery_table" "vw_monthly_chargeback" {
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
  table_id   = "vw_monthly_agent_chargeback"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_monthly_agent_chargeback.sql.tpl", {
      dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.token_events]
}

# Per-Request Level Telemetry View (Lists every individual request & session)
resource "google_bigquery_table" "vw_request_level_telemetry" {
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
  table_id   = "vw_request_level_telemetry"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_request_level_telemetry.sql.tpl", {
      dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.token_events]
}

# Security & Compliance Audit Trail View (IAM changes, secret accesses, deployments)
resource "google_bigquery_table" "vw_security_audit_trail" {
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
  table_id   = "vw_security_audit_trail"
  project    = var.governance_project_id

  view {
    query          = file("${path.module}/../../sql/vw_security_audit_trail.sql")
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.token_events]
}
