# --- GATEWAY CONTRACT ---
# Every gateway module under modules/5-workloads/gateways/ exposes the same outputs, so the
# layer-5 entrypoint can route any host to any gateway.

output "backend_service_id" {
  description = "ID of the regional INTERNAL_MANAGED backend service fronting Kong; the entrypoint URL map routes hosts to it"
  value       = google_compute_region_backend_service.kong_backend.id
}

output "routes" {
  description = "Hosts Kong serves (<name>.esmeralda.internal, matching templates/kong.yml.tpl) and how `make test-gateway` probes each one"
  value = {
    for key, endpoint in var.agent_endpoints : endpoint.logical_name => {
      host  = "${endpoint.logical_name}.esmeralda.internal"
      probe = endpoint.probe
    }
  }
}
