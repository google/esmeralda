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

output "ai_coe_agents_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  value       = local.ai_coe_agents_id
}

output "cx_agents_project_id" {
  description = "The project ID allocated for CX team agents (cx-agents project)"
  value       = local.cx_agents_id
}

output "governance_project_id" {
  description = "The active project ID hosting central governance, encryption, secrets, and telemetry"
  value       = local.governance_id
}

output "project_suffix" {
  description = "The random project suffix generated in Layer 1"
  value       = local.suffix
}

output "mcps_run_service_agent" {
  description = "The Cloud Run Service Agent email in MCPS project"
  value       = google_project_service_identity.mcps_run.email
}


output "gateway_run_service_agent" {
  description = "The Cloud Run Service Agent email in Gateway project"
  value       = google_project_service_identity.gateway_run.email
}

output "ai_coe_agents_run_service_agent" {
  description = "The Cloud Run Service Agent email in the AI CoE agents project"
  value       = google_project_service_identity.ai_coe_agents_run.email
}

output "ai_coe_agents_vertex_service_agent" {
  description = "The Vertex AI Service Agent email in the AI CoE agents project"
  value       = google_project_service_identity.ai_coe_agents_vertex.email
}

output "cx_agents_vertex_service_agent" {
  description = "The Vertex AI Service Agent email in CX agents project"
  value       = google_project_service_identity.cx_agents_vertex.email
}

output "cx_agents_run_service_agent" {
  description = "The Cloud Run Service Agent email in CX agents project"
  value       = google_project_service_identity.cx_agents_run.email
}


output "ai_coe_agents_sql_service_agent" {
  description = "The Cloud SQL Service Agent email in the AI CoE agents project"
  value       = google_project_service_identity.ai_coe_agents_sql.email
}

output "governance_secrets_service_agent" {
  description = "The Secret Manager Service Agent email in Governance project"
  value       = var.byo_governance_project ? "service-${data.google_project.governance[0].number}@gcp-sa-secretmanager.iam.gserviceaccount.com" : try(google_project_service_identity.governance_secrets[0].email, "")
}

