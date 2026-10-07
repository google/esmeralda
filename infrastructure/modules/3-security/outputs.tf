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

output "ai_coe_mortgage_specialist_sa_email" {
  description = "The email address of the A2A agent service account"
  value       = google_service_account.ai_coe_mortgage_specialist_sa.email
}

output "cx_mortgage_orchestrator_sa_email" {
  description = "The email address of the CX mortgage orchestrator service account"
  value       = google_service_account.cx_mortgage_orchestrator_sa.email
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

# --------------------------------------------------------------------
# Internal PKI (Root CA for *.esmeralda.internal)
# --------------------------------------------------------------------

output "internal_ca_cert_pem" {
  description = "PEM of the internal Root CA. Trusted by the Agent Gateway (TrustConfig) and the agents."
  value       = tls_self_signed_cert.internal_ca.cert_pem
}

output "internal_ca_key_pem" {
  description = "Private key of the internal Root CA, used by layer 5 (Kong) to sign its *.esmeralda.internal leaf."
  value       = tls_private_key.internal_ca.private_key_pem
  sensitive   = true
}

output "internal_ca_secret_id" {
  description = "Secret Manager secret (gateway project) publishing the internal Root CA certificate."
  value       = google_secret_manager_secret.internal_ca.id
}
