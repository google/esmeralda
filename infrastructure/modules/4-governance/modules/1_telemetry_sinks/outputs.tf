output "sink_writer_identities" {
  value       = { for k, v in google_logging_project_sink.central_sinks : k => v.writer_identity }
  description = "Map of log sink writer service account identities per project"
}

output "dataset_id" {
  value       = google_bigquery_dataset.telemetry_logs.dataset_id
  description = "ID of the central BigQuery telemetry dataset"
}
