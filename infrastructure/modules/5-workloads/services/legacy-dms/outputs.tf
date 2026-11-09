output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Legacy DMS MCP server"
  value       = module.mcp_service.service_uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = module.mcp_service.service_name
}

output "registry_service_id" {
  description = "Agent Registry service (governance project) publishing this MCP server"
  value       = module.mcp_service.registry_service_id
}

output "service_account_email" {
  description = "The dedicated per-service workload service account email"
  value       = module.mcp_service.service_account_email
}
