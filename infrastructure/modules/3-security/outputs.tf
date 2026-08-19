# ====================================================================
# 6. OUTPUTS SPECIFICATION
# ====================================================================

output "database_key_id" {
  description = "The fully qualified crypto key ID for private database CMEK"
  value       = local.resolved_database_key_id
}

output "secrets_key_id" {
  description = "The fully qualified crypto key ID for secret payloads CMEK"
  value       = local.resolved_secrets_key_id
}

output "db_password_secret_name" {
  description = "The Secret Manager resource path representing the DB admin credentials"
  value       = local.resolved_db_password_secret_id
}

output "mcps_sa_email" {
  description = "The email address of the MCP tools server service account"
  value       = google_service_account.mcps_sa.email
}

output "cicd_builder_sa_email" {
  description = "The email address of the dedicated Cloud Build container delivery service account in CI/CD project"
  value       = local.builder_sa_email
}

output "mcps_builder_sa_email" {
  description = "Alias for backwards compatibility"
  value       = local.builder_sa_email
}


output "a2a_agent_sa_email" {
  description = "The email address of the A2A agent service account"
  value       = google_service_account.a2a_sa.email
}

output "root_agent_sa_email" {
  description = "The email address of the Root Orchestrator service account"
  value       = google_service_account.root_sa.email
}

output "test_vm_sa_email" {
  description = "The email address of the dedicated debugging Test VM service account"
  value       = google_service_account.test_vm_sa.email
}

output "kong_sa_email" {
  description = "The email address of the Kong Gateway service account"
  value       = google_service_account.kong_sa.email
}

output "telemetry_dataset_id" {
  description = "The BigQuery dataset ID capturing agentic telemetry and audit logs"
  value       = "esmeralda_telemetry_logs_${var.environment}"
}

output "mcp_invoker_sa_email" {
  description = "The email address of the shared MCP invoker service account"
  value       = google_service_account.mcp_invoker_sa.email
}
