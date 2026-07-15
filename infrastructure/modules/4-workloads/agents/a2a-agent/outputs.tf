output "engine_id" {
  description = "The fully qualified unique resource name of the deployed A2A Reasoning Engine"
  value       = google_vertex_ai_reasoning_engine.agent.id
}

output "endpoint_url" {
  description = "The internal GCP API Endpoint address allocated for executing predictions against A2A"
  value       = "https://${var.region}-aiplatform.googleapis.com/v1beta1/${google_vertex_ai_reasoning_engine.agent.id}"
}

output "db_connection_name" {
  description = "The connection string identifier for the atomic Cloud SQL postgres database"
  value       = google_sql_database_instance.task_store.connection_name
}

output "db_private_ip" {
  description = "The private internal IP address allocated for the database"
  value       = google_sql_database_instance.task_store.private_ip_address
}

output "staging_bucket_name" {
  description = "The name of the atomic GCS bucket used for staging code dependencies"
  value       = google_storage_bucket.staging.name
}

output "artifacts_bucket_name" {
  description = "The name of the atomic GCS bucket used for runtime task artifacts"
  value       = google_storage_bucket.artifacts.name
}

output "logs_bucket_name" {
  description = "The name of the atomic GCS bucket used for long-term logs offload"
  value       = google_storage_bucket.logs.name
}
