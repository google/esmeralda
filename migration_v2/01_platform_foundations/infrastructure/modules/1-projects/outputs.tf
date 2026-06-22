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
