output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Income Verification MCP server"
  value       = google_cloud_run_v2_service.income_verification.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.income_verification.name
}
