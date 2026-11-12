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

output "notification_channels" {
  value       = compact([try(google_monitoring_notification_channel.email_alert[0].name, ""), google_monitoring_notification_channel.pubsub_alert.name])
  description = "Notification channel names of the governance alerts (reused by layer-5 alerts such as the gateway attribution count)"
}
