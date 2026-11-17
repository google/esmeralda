# --- FRONTEND CONTRACT ---
# Like a gateway adapter: a regional INTERNAL_MANAGED backend service that the layer-5 entrypoint
# routes one host to (<host_label>.<domain>). No load balancer, certificate or DNS record here.

output "backend_service_id" {
  description = "Regional INTERNAL_MANAGED backend service (serverless NEG, IAP on) the entry point routes <host_label>.<domain> to"
  value       = google_compute_region_backend_service.console.id
}

output "host_label" {
  description = "Host label of the console on the entry point"
  value       = var.host_label
}

output "url" {
  description = "Console URL (private: reachable only from networks that reach the entry point, and only for iap_accessors)"
  value       = "https://${var.host_label}.${var.domain}/"
}

output "iap_audience" {
  description = "Expected aud of the IAP JWT, passed to the app as ESMERALDA_UI_IAP_AUDIENCE"
  value       = local.iap_audience
}

output "service_name" {
  description = "Cloud Run service name (the running-image check reads it)"
  value       = google_cloud_run_v2_service.console.name
}

output "service_project_id" {
  description = "Project of the Cloud Run service (the gateway project)"
  value       = var.project_id
}

output "service_account_email" {
  description = "Runtime service account of the console: the identity of the catalog caller esmeralda-ui"
  value       = google_service_account.console.email
}

output "image" {
  description = "Deployed image, by digest"
  value       = local.image_by_digest
}
