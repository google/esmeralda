output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Corporate Email MCP server"
  value       = google_cloud_run_v2_service.corporate_email.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.corporate_email.name
}
