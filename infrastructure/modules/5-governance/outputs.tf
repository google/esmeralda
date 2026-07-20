# Outputs for Stage 5 Governance Stack
output "governance_dataset_id" {
  value       = "esmeralda_telemetry_logs_${var.environment}"
  description = "BigQuery dataset ID for telemetry and chargeback analytics"
}

output "monitoring_pubsub_topic" {
  value       = module.alert_policies.pubsub_topic_id
  description = "Pub/Sub topic ID for real-time monitoring alert push notifications"
}

output "dlp_inspect_template_id" {
  value       = module.dlp_inspection.inspect_template_id
  description = "Cloud DLP PII inspection template ID"
}

output "token_events_table_id" {
  value       = module.finops_analytics.token_events_table_id
  description = "BigQuery token events table ID"
}

output "chargeback_view_id" {
  value       = module.finops_analytics.chargeback_view_id
  description = "BigQuery FinOps monthly chargeback view ID"
}

output "golden_signals_dashboard_id" {
  value       = module.alert_policies.golden_signals_dashboard_id
  description = "Cloud Monitoring Agent Golden Signals Dashboard ID"
}

output "finops_dashboard_id" {
  value       = module.alert_policies.finops_dashboard_id
  description = "Cloud Monitoring FinOps Token Analytics Dashboard ID"
}
