output "url_map_id" {
  description = "ID of the entry point regional URL map"
  value       = google_compute_region_url_map.entrypoint.id
}

output "ip_address" {
  description = "Internal IP the entry point listens on (the layer-2 reserved address)"
  value       = google_compute_forwarding_rule.entrypoint.ip_address
}

output "routing" {
  description = "Effective routing: the default gateway and the FQDN => gateway overrides"
  value = {
    default    = var.default_backend
    host_rules = { for host, backend in var.host_rules : local.host_fqdns[host] => backend }
  }
}
