output "chargeback_view_id" {
  value       = try(google_bigquery_table.vw_monthly_chargeback[0].table_id, "pending_initial_agent_traffic")
  description = "BigQuery FinOps monthly chargeback view ID"
}

output "unified_events_table_id" {
  value       = google_bigquery_table.unified_telemetry_events.table_id
  description = "Unified BigQuery telemetry event envelope table ID"
}
