output "chargeback_view_id" {
  value       = google_bigquery_table.vw_monthly_chargeback.table_id
  description = "BigQuery FinOps monthly chargeback view ID"
}

output "unified_events_table_id" {
  value       = google_bigquery_table.unified_telemetry_events.table_id
  description = "Unified BigQuery telemetry event envelope table ID"
}

output "usage_events_view_id" {
  value       = google_bigquery_table.usage_events.table_id
  description = "BigQuery view of every esmeralda.usage record, flattened"
}

output "usage_normalized_view_id" {
  value       = google_bigquery_table.vw_usage_normalized.table_id
  description = "BigQuery view unioning native usage records and the legacy token-event shapes, with attribution"
}

output "principal_teams_table_id" {
  value       = google_bigquery_table.principal_teams.table_id
  description = "BigQuery principal -> team attribution table, filled by the catalog compiler"
}
