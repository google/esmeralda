output "gateway_id" {
  description = "The ID of the deployed Agent Gateway resource"
  value       = google_network_services_agent_gateway.egress_gateway.id
}

output "gateway_name" {
  description = "The name of the deployed Agent Gateway"
  value       = google_network_services_agent_gateway.egress_gateway.name
}

output "network_attachment_id" {
  description = "The ID of the compute network attachment used by the Agent Gateway"
  value       = google_compute_network_attachment.agent_gateway.id
}

output "iap_policy_id" {
  description = "The ID of the IAP authorization policy targeting the Agent Gateway"
  value       = google_network_security_authz_policy.iap_policy.id
}
