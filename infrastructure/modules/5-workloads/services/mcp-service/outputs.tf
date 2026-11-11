output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the MCP server"
  value       = google_cloud_run_v2_service.service.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.service.name
}

output "registry_service_id" {
  description = "Agent Registry service (governance project) publishing this MCP server"
  value       = google_agent_registry_service.governance_mcp.id
}

output "service_account_email" {
  description = "The dedicated per-service workload service account email"
  value       = google_service_account.service_sa.email
}

output "caller_guard" {
  description = "Whether the catalog caller guard is on (ESMERALDA_CALLER_ALLOWLIST set on the service)"
  value       = var.caller_allowlist != null
}
