# infrastructure/modules/4-governance/modules/6_agent_gateway/outputs.tf

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

output "agw_root_ca_bundle" {
  value       = local.agw_root_ca_bundle
  description = "PEM bundle agents must trust: Agent Gateway TLS-inspection roots + internal Root CA."
}

output "iap_egress_members" {
  value       = local.iap_egress_members
  description = "IAM members of the roles/iap.egressor policy on each baseline Google API entry (the agent projects' principalSets); the registry root holds no binding."
}

output "system_endpoint_ids" {
  value       = local.baseline_endpoint_ids
  description = "Agent Registry service ids of the baseline Google API entries, the ones that list iap_egress_members."
}

output "google_api_endpoint_ids" {
  value       = sort(keys(local.google_api_endpoints))
  description = "Agent Registry service ids of every Google API entry (baseline and per-agent), from the catalog google_apis."
}
