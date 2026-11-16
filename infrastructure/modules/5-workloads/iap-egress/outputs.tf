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

output "runtime_catalog_secret_id" {
  description = "Secret Manager secret ID storing the bound runtime catalog payload (esmeralda-runtime-catalog-<env>)"
  value       = try(google_secret_manager_secret.runtime_catalog[0].secret_id, "")
}

output "runtime_catalog_secret_name" {
  description = "Full Secret Manager resource name storing the bound runtime catalog payload"
  value       = try(google_secret_manager_secret.runtime_catalog[0].name, "")
}

output "runtime_catalog_payload" {
  description = "Bound runtime catalog document (esmeralda.runtime_catalog v1) with resolved endpoints, caller principals, and active governance capabilities"
  value       = local.runtime_catalog_payload
}
