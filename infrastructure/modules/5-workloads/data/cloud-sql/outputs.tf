output "enabled" {
  description = "Whether the Cloud SQL task store instance is enabled"
  value       = var.enabled
}

output "instance_connection_name" {
  description = "The connection string identifier for the Cloud SQL PostgreSQL database"
  value       = var.enabled ? google_sql_database_instance.task_store[0].connection_name : ""
}

output "db_secret_id" {
  description = "The Secret Manager secret resource ID storing database connection metadata"
  value       = var.enabled ? google_secret_manager_secret.db_Connection[0].id : ""
}

output "db_private_ip" {
  description = "The private internal IP address allocated for the database"
  value       = var.enabled ? google_sql_database_instance.task_store[0].private_ip_address : ""
}

output "database_name" {
  description = "The provisioned database name"
  value       = var.enabled ? google_sql_database.agent_tasks[0].name : ""
}
