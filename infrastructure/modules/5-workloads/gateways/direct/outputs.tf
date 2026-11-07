# --- DIRECT CONTRACT ---
# Unlike an adapter (one backend_service_id for every host sent to it), direct gives the entry
# point one backend per host, plus the rewrite and timeout each agent route needs.

output "hosts" {
  description = "Catalog name => backend_service_id, host_rewrite, timeout_sec and path_rewrites (match = prefix | full, path, rewrite) for the entry point route of <name>.<domain>"
  value = merge(
    {
      for name, service in var.mcp_services : name => {
        backend_service_id = google_compute_region_backend_service.mcp[name].id
        host_rewrite       = null
        timeout_sec        = null
        path_rewrites      = []
      }
    },
    {
      for name, agent in var.agents : name => {
        backend_service_id = google_compute_region_backend_service.aiplatform[0].id
        host_rewrite       = local.aiplatform_host
        timeout_sec        = agent.timeout_sec
        path_rewrites = agent.kind == "a2a" ? [
          # A2A (card, message:send, ...): /v1/<x> => <engine>/a2a/v1/<x>
          { match = "prefix", path = "/v1/", rewrite = "${local.engine_paths[name]}/a2a/v1/" }
          ] : [
          # ADK: the path `esmeralda serve` exposes locally => <engine>:streamQuery (newline-delimited
          # JSON chunks, streamed; query strings such as ?alt=sse pass through unchanged)
          { match = "full", path = "/api/stream_reasoning_engine", rewrite = "${local.engine_paths[name]}:streamQuery" }
        ]
      }
    }
  )
}

output "default_backend_service_id" {
  description = "Backend for hosts outside the catalog when direct is the default (they get a 404): the aiplatform backend, else the first MCP backend"
  value = (
    length(var.agents) > 0 ? google_compute_region_backend_service.aiplatform[0].id :
    length(var.mcp_services) > 0 ? google_compute_region_backend_service.mcp[sort(keys(var.mcp_services))[0]].id : null
  )
}
