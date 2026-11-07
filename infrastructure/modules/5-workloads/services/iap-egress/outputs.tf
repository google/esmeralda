# infrastructure/modules/5-workloads/services/iap-egress/outputs.tf

output "registry_iap_egress_members" {
  description = "Normalized IAM members granted roles/iap.egressor on the central Agent Registry root (system endpoints)"
  value       = local.normalized_registry_members
}

output "target_iap_members" {
  description = "Normalized per-target IAM members granted roles/iap.egressor on each MCP server and A2A agent"
  value       = local.normalized_target_iap_members
}
