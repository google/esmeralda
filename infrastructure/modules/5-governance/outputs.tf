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

output "unified_events_table_id" {
  value       = module.finops_analytics.unified_events_table_id
  description = "Unified BigQuery telemetry event envelope table ID"
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

output "model_armor_prompt_template_id" {
  value       = module.model_armor.prompt_template_id
  description = "Model Armor Prompt Inbound Guardrails Template ID"
}

output "model_armor_prompt_template_name" {
  value       = module.model_armor.prompt_template_name
  description = "Model Armor Prompt Inbound Guardrails Template Full Resource Name"
}

output "model_armor_response_template_id" {
  value       = module.model_armor.response_template_id
  description = "Model Armor Response Outbound Guardrails Template ID"
}

output "model_armor_response_template_name" {
  value       = module.model_armor.response_template_name
  description = "Model Armor Response Outbound Guardrails Template Full Resource Name"
}
