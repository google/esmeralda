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
  description = "The workload service account email of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.service.template[0].service_account
}
