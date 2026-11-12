output "sink_writer_identities" {
  value       = { for k, v in google_logging_project_sink.central_sinks : k => v.writer_identity }
  description = "Map of log sink writer service account identities per project"
}

output "usage_dataset_id" {
  value       = google_bigquery_dataset.usage_events.dataset_id
  description = "ID of the BigQuery dataset holding the esmeralda.usage landing tables"
}

output "usage_landing_table_ids" {
  # Read from the resources so consumers (the usage_events view) wait for the tables to exist.
  value       = [for t in var.usage_landing_tables : google_bigquery_table.usage_landing[t].table_id]
  description = "esmeralda.usage landing tables, in var.usage_landing_tables order"
}

output "usage_sink_filter" {
  value       = local.usage_sink_filter
  description = "Log filter of the usage sinks (keyed on jsonPayload.schema, never on a log name)"
}

output "dataset_id" {
  value       = google_bigquery_dataset.telemetry_logs.dataset_id
  description = "ID of the central BigQuery telemetry dataset"
}
