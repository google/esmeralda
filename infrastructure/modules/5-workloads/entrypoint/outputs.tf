output "url_map_id" {
  description = "ID of the entry point regional URL map"
  value       = google_compute_region_url_map.entrypoint.id
}

output "ip_address" {
  description = "Internal IP the entry point listens on (the layer-2 reserved address)"
  value       = google_compute_forwarding_rule.entrypoint.ip_address
}

output "routing" {
  description = "Effective routing: the default backend and the FQDN => backend overrides"
  value = {
    default    = var.default_backend
    host_rules = { for label, backend in local.rules : local.fqdn[label] => backend }
  }
}

output "routes" {
  description = "Every catalog host, its backend (direct or an adapter), the token kind callers send and how `make test-gateway` probes it. Adapters front Cloud Run IAM, so hosts routed to one take ID tokens"
  value = {
    for name, entry in var.catalog : name => {
      host    = "${name}.${var.domain}"
      kind    = entry.kind
      probe   = entry.probe
      backend = local.routing[name]
      token   = local.routing[name] == "direct" ? entry.token : "id"
    }
  }
}
