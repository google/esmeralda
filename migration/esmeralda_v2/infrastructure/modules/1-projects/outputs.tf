# ====================================================================
# 3. OUTPUTS SPECIFICATION
# ====================================================================

output "net_host_project_id" {
  description = "The active project ID hosting the Shared VPC network"
  value       = local.net_host_id
}

output "gateway_project_id" {
  description = "The active project ID hosting the API Ingress Gateway"
  value       = local.gateway_id
}

output "mcps_project_id" {
  description = "The project ID allocated for corporate MCP servers"
  value       = local.mcps_id
}

output "a2a_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  value       = local.a2a_id
}

output "root_project_id" {
  description = "The project ID allocated for client-facing LOB Root agent"
  value       = local.root_agent_id
}

output "governance_project_id" {
  description = "The active project ID hosting central governance, encryption, secrets, and telemetry"
  value       = local.governance_id
}

output "project_suffix" {
  description = "The random project suffix generated in Stage 1"
  value       = local.suffix
}

output "mcps_run_service_agent" {
  description = "The Cloud Run Service Agent email in MCPS project"
  value       = google_project_service_identity.mcps_run.email
}

output "a2a_run_service_agent" {
  description = "The Cloud Run Service Agent email in A2A project"
  value       = google_project_service_identity.a2a_run.email
}

output "a2a_vertex_service_agent" {
  description = "The Vertex AI Service Agent email in A2A project"
  value       = google_project_service_identity.a2a_vertex.email
}

output "root_vertex_service_agent" {
  description = "The Vertex AI Service Agent email in Root Agent project"
  value       = google_project_service_identity.root_vertex.email
}

output "a2a_sql_service_agent" {
  description = "The Cloud SQL Service Agent email in A2A project"
  value       = google_project_service_identity.a2a_sql.email
}

output "governance_secrets_service_agent" {
  description = "The Secret Manager Service Agent email in Governance project"
  value       = var.byo_governance_project ? "service-${data.google_project.governance[0].number}@gcp-sa-secretmanager.iam.gserviceaccount.com" : try(google_project_service_identity.governance_secrets[0].email, "")
}

