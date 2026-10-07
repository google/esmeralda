output "pubsub_topic_id" {
  value       = google_pubsub_topic.monitoring_alerts_topic.id
  description = "Pub/Sub topic ID for monitoring alert push notifications"
}

output "golden_signals_dashboard_id" {
  value       = google_monitoring_dashboard.agent_golden_signals.id
  description = "Cloud Monitoring Agent Golden Signals Dashboard ID"
}

output "finops_dashboard_id" {
  value       = google_monitoring_dashboard.finops_token_analytics.id
  description = "Cloud Monitoring FinOps Token Analytics Dashboard ID"
}
