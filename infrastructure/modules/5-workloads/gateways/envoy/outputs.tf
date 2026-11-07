# --- GATEWAY CONTRACT ---
# Every gateway module under modules/5-workloads/gateways/ exposes the same outputs, so the
# layer-5 entrypoint can route any host to any gateway.

output "backend_service_id" {
  description = "ID of the regional INTERNAL_MANAGED backend service fronting Envoy; the entrypoint URL map routes hosts to it"
  value       = google_compute_region_backend_service.gateway.id
}

output "routes" {
  description = "Hosts Envoy serves (<name>.<apex_domain>, matching the config submodule) and how `make test-gateway` probes each one"
  value = {
    for key, endpoint in var.agent_endpoints : endpoint.logical_name => {
      host  = "${endpoint.logical_name}.${var.apex_domain}"
      probe = endpoint.probe
    }
  }
}

output "service_name" {
  description = "The gateway's Cloud Run service (envoy-gateway-<env>)"
  value       = google_cloud_run_v2_service.gateway.name
}

output "hostnames" {
  description = "Every hostname the gateway serves: the apex domain and <name>.<apex_domain> per target"
  value       = module.config.hostnames
}
