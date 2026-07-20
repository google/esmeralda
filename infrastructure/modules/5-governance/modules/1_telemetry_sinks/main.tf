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

  # Filter stdout/stderr logs from Reasoning Engines and Cloud Run microservices
  filter = "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" OR logName=~\"gen_ai\" OR logName=~\"reasoning_engine_stdout\" OR logName=~\"reasoning_engine_stderr\" OR resource.type=\"cloud_run_revision\""

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
