output "token_events_table_id" {
  value       = google_bigquery_table.token_events.table_id
  description = "BigQuery token events table ID"
}

output "chargeback_view_id" {
  value       = google_bigquery_table.vw_monthly_chargeback.table_id
  description = "BigQuery FinOps monthly chargeback view ID"
}

output "unified_events_table_id" {
  value       = google_bigquery_table.unified_telemetry_events.table_id
  description = "Unified BigQuery telemetry event envelope table ID"
}
