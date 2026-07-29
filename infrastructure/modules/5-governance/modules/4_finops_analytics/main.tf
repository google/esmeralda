# Reference Central BigQuery Telemetry Dataset
data "google_bigquery_dataset" "telemetry_logs" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  project    = var.governance_project_id
}

# Unified Event Envelope Telemetry Table for Infinite Multi-Event Scaling
resource "google_bigquery_table" "unified_telemetry_events" {
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
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
  dataset_id          = data.google_bigquery_dataset.telemetry_logs.dataset_id
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

  depends_on = [google_bigquery_table.unified_telemetry_events]
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

  depends_on = [google_bigquery_table.unified_telemetry_events]
}

# Security & Compliance Audit Trail View (IAM changes, secret accesses, deployments)
resource "google_bigquery_table" "vw_security_audit_trail" {
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
  table_id   = "vw_security_audit_trail"
  project    = var.governance_project_id

  view {
    query = templatefile("${path.module}/../../sql/vw_security_audit_trail.sql.tpl", {
      dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
    })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.cloudaudit_activity]
}
