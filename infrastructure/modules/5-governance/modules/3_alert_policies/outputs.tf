output "pubsub_topic_id" {
  value       = google_pubsub_topic.monitoring_alerts_topic.id
  description = "Pub/Sub topic ID for monitoring alerts"
}

output "runaway_alert_policy_id" {
  value       = google_monitoring_alert_policy.runaway_loop_token_cap.id
  description = "Alert policy ID for token cap runaway loops"
}

output "reasoning_engine_alert_policy_id" {
  value       = google_monitoring_alert_policy.reasoning_engine_quota.id
  description = "Alert policy ID for Reasoning Engine quota limits"
}
