locals {
  monitored_projects = toset(var.spoke_project_ids)
}

# Reference Central BigQuery Telemetry Dataset
data "google_bigquery_dataset" "telemetry_logs" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  project    = var.governance_project_id
}

# Deploy Central Log Sinks across all workload spoke projects
resource "google_logging_project_sink" "central_sinks" {
  for_each    = local.monitored_projects
  name        = "esmeralda-central-telemetry-sink-${var.environment}"
  project     = each.value
  destination = "bigquery.googleapis.com/projects/${var.governance_project_id}/datasets/${data.google_bigquery_dataset.telemetry_logs.dataset_id}"

  # Filter stdout/stderr logs from Reasoning Engines, Cloud Run microservices, and Cloud Audit logs
  filter = "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" OR logName=~\"gen_ai\" OR logName=~\"reasoning_engine_stdout\" OR logName=~\"reasoning_engine_stderr\" OR resource.type=\"cloud_run_revision\" OR logName=~\"cloudaudit.googleapis.com\""

  bigquery_options {
    use_partitioned_tables = true
  }

  exclusions {
    name        = "exclude-debug-logs"
    description = "Exclude verbose debug log entries to minimize BigQuery ingestion costs"
    filter      = "severity < INFO AND NOT jsonPayload.event=\"genai_token_consumption\""
  }

  unique_writer_identity = true
}

# Authorize each Logging sink writer identity to insert rows into BigQuery
resource "google_bigquery_dataset_iam_member" "dataset_writers" {
  for_each   = google_logging_project_sink.central_sinks
  dataset_id = data.google_bigquery_dataset.telemetry_logs.dataset_id
  project    = var.governance_project_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value.writer_identity
}

# ------------------------------------------------------------------------------
# GCS LONG-TERM TELEMETRY ARCHIVAL BUCKET (Coldline 7-Year Regulatory Audit Retention)
# ------------------------------------------------------------------------------
resource "google_storage_bucket" "telemetry_archive" {
  name                        = "esmeralda-telemetry-archive-${var.governance_project_id}"
  project                     = var.governance_project_id
  location                    = "us-central1"
  storage_class               = "COLDLINE"
  uniform_bucket_level_access = true

  retention_policy {
    is_locked        = false
    retention_period = 220898400 # 7 Years (in seconds)
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 2555 # 7 Years (365 * 7)
    }
  }
}

# GCS Storage Object Creator IAM binding for sink writer identities
resource "google_storage_bucket_iam_member" "gcs_sink_writers" {
  for_each = google_logging_project_sink.central_sinks
  bucket   = google_storage_bucket.telemetry_archive.name
  role     = "roles/storage.objectCreator"
  member   = each.value.writer_identity
}
