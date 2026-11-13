# infrastructure/modules/5-workloads/iap-egress/outputs.tf

output "target_iap_members" {
  description = "Normalized per-target IAM members granted roles/iap.egressor on each agent, external endpoint and MCP server entry (`esmeralda verify` compares the live policies with it)"
  value       = local.normalized_target_iap_members
}

output "external_registry_entries" {
  description = "Agent Registry entries of the catalog's managed: false targets: service_id => { registry, url } (placeholders substituted)"
  value = {
    for name, t in var.external_targets : name => {
      registry = t.registry
      url      = local.external_urls[name]
    }
  }
}
