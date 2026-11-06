output "gateway_ingress_ip" {
  description = "The internal VIP of the gateway's HTTPS load balancer (what *.esmeralda.internal resolves to)"
  value       = google_compute_forwarding_rule.gateway.ip_address
}

output "gateway_agent_ingress_host" {
  description = "The private DNS zone the gateway serves"
  value       = var.apex_domain
}

output "hostnames" {
  description = "Every hostname the gateway serves"
  value       = module.config.hostnames
}

output "service_name" {
  description = "The gateway's Cloud Run service"
  value       = google_cloud_run_v2_service.gateway.name
}
