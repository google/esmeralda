# infrastructure/modules/5-workloads/iap-egress/outputs.tf

output "registry_iap_egress_members" {
  description = "Normalized IAM members of the registry-root roles/iap.egressor binding (conditioned to system endpoint entries only)"
  value       = local.normalized_registry_members
}

output "target_iap_members" {
  description = "Normalized per-target IAM members granted roles/iap.egressor on each MCP server and A2A agent"
  value       = local.normalized_target_iap_members
}
