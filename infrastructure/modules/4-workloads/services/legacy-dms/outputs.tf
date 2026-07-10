output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Legacy DMS MCP server"
  value       = google_cloud_run_v2_service.legacy_dms.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.legacy_dms.name
}
