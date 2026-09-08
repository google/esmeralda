# infrastructure/modules/5-governance/modules/6_agent_gateway/outputs.tf

output "agent_gateway_id" {
  value       = try(google_network_services_agent_gateway.egress_gateway[0].id, "")
  description = "The fully qualified resource ID of the Central Agent Gateway."
}

output "agent_gateway_name" {
  value       = try(google_network_services_agent_gateway.egress_gateway[0].name, "")
  description = "The name of the Central Agent Gateway."
}

output "ca_cert_secret_id" {
  value       = try(google_secret_manager_secret.agw_ca_cert[0].secret_id, "")
  description = "Secret Manager secret ID containing the Gateway CA root certificate."
}

output "ca_cert_secret_name" {
  value       = try(google_secret_manager_secret.agw_ca_cert[0].id, "")
  description = "Secret Manager secret resource name."
}
