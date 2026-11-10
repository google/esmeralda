# ====================================================================
# Built-in "direct" backends of the entry point (no proxy)
# --------------------------------------------------------------------
# The default way the layer-5 entry point reaches the catalog (env.yaml `catalog`): the internal
# Application Load Balancer talks to each service itself, and every caller sends its own credential.
#
#   - MCP services (Cloud Run): one serverless NEG + backend service per service, created in the
#     service's own project. A serverless NEG can only point at a Cloud Run service in its own
#     project, so the entry point URL map (gateway project) references these backend services
#     cross-project. Callers send a Google ID token (audience https://<name>.<domain>), checked by
#     Cloud Run IAM against the service's invokers and custom audiences.
#   - Agents (Agent Runtime): one HTTPS backend service in the gateway project, shared by every
#     agent. Each agent host gets a route that rewrites the host and the path prefix to its engine
#     (see the `hosts` output). Callers send an OAuth2 access token (Agent Runtime rejects ID
#     tokens). Two backend types (var.agent_backend):
#       psc:      a Private Service Connect NEG to the regional aiplatform API. Its backend service
#                 timeout is fixed at 30 s and, even with a longer route timeout, bounds the time to
#                 the first response byte: a unary call (A2A message:send) that answers after 30 s
#                 gets a 504. Streams pass, since headers arrive early.
#       hybrid:   a hybrid NEG pointing at the Shared VPC's Private Service Connect endpoint for
#                 Google APIs (layer 2). Same aiplatform front end, still private, but its backend
#                 service timeout is configurable: it is set to the agents' longest route timeout,
#                 so long unary calls complete. (A regional internet NEG would also allow it, but
#                 org policies commonly enforce compute.disableInternetNetworkEndpointGroup.)
#
# Nothing here swaps or adds credentials, so each service sees the real caller's identity.
# ====================================================================

locals {
  aiplatform_host = "${var.region}-aiplatform.googleapis.com"

  # Engine resource path of each agent: "/v1beta1/projects/<p>/locations/<r>/reasoningEngines/<id>".
  engine_paths = { for name, agent in var.agents : name => replace(agent.endpoint_url, "/^https://[^/]+/", "") }

  create_hybrid = length(var.agents) > 0 && var.hybrid_agent_backend
  hybrid_zone   = var.hybrid_neg_zone != "" ? var.hybrid_neg_zone : "${var.region}-a"

  # The hybrid backend serves every agent route, so its timeout is the longest route timeout.
  hybrid_timeout_sec = length(var.agents) > 0 ? max([for agent in values(var.agents) : agent.timeout_sec]...) : 600

  agent_backend_service_id = (
    length(var.agents) == 0 ? null :
    var.agent_backend == "hybrid" ? try(google_compute_region_backend_service.aiplatform_hybrid[0].id, null) :
    google_compute_region_backend_service.aiplatform[0].id
  )
}

# --- MCP services: serverless NEG + backend service in the service's project ---

resource "google_compute_region_network_endpoint_group" "mcp" {
  for_each = var.mcp_services

  name                  = "neg-direct-${each.key}-${var.environment}"
  project               = each.value.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = each.value.service_name
  }
}

# No timeout_sec: it can't be set on serverless NEG backends; the Cloud Run request timeout applies.
resource "google_compute_region_backend_service" "mcp" {
  for_each = var.mcp_services

  name                  = "bs-direct-${each.key}-${var.environment}"
  project               = each.value.project_id
  region                = var.region
  protocol              = "HTTPS"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.mcp[each.key].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# --- Agents: one PSC NEG to the regional aiplatform API + one backend service (gateway project) ---

# Regional PSC NEGs for Google APIs take no network or subnetwork. The `.rep.` regional endpoint
# hostnames are not accepted as targets: use <region>-aiplatform.googleapis.com.
resource "google_compute_region_network_endpoint_group" "aiplatform" {
  count = length(var.agents) > 0 ? 1 : 0

  name                  = "neg-direct-aiplatform-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "PRIVATE_SERVICE_CONNECT"
  psc_target_service    = local.aiplatform_host
}

# No timeout_sec: a backend service with a PSC NEG that targets a Google API has a fixed 30 s
# timeout, which bounds the time to the first response byte even when the route timeout is longer.
# Streams pass (headers arrive early); a unary answer after 30 s gets a 504 (use agent_backend = internet).
resource "google_compute_region_backend_service" "aiplatform" {
  count = length(var.agents) > 0 ? 1 : 0

  name                  = "bs-direct-aiplatform-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTPS"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.aiplatform[0].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# --- Agents, hybrid backend: hybrid NEG to the PSC endpoint for Google APIs, real timeout ---

resource "google_compute_network_endpoint_group" "aiplatform_hybrid" {
  count = local.create_hybrid ? 1 : 0

  name                  = "neg-direct-aiplatform-hyb-${var.environment}"
  project               = var.project_id
  zone                  = local.hybrid_zone
  network               = var.network_id
  network_endpoint_type = "NON_GCP_PRIVATE_IP_PORT"
  default_port          = 443

  lifecycle {
    precondition {
      condition     = var.network_id != null && var.network_id != "" && var.googleapis_psc_ip != ""
      error_message = "hybrid_agent_backend needs network_id and googleapis_psc_ip (layer 2, enable_googleapis_psc_endpoint)."
    }
  }
}

resource "google_compute_network_endpoint" "aiplatform_hybrid" {
  count = local.create_hybrid ? 1 : 0

  project                = var.project_id
  zone                   = local.hybrid_zone
  network_endpoint_group = google_compute_network_endpoint_group.aiplatform_hybrid[0].name
  ip_address             = var.googleapis_psc_ip
  port                   = 443
}

# Hybrid NEG backends need a health check; regional load balancers probe from their proxies
# (distributed Envoy health checks, from the proxy-only subnet). TCP 443 on the PSC endpoint.
resource "google_compute_region_health_check" "aiplatform_hybrid" {
  count = local.create_hybrid ? 1 : 0

  name    = "hc-direct-aiplatform-hyb-${var.environment}"
  project = var.project_id
  region  = var.region

  tcp_health_check {
    port = 443
  }
}

resource "google_compute_region_backend_service" "aiplatform_hybrid" {
  count = local.create_hybrid ? 1 : 0

  name                  = "bs-direct-aiplatform-hyb-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTPS"
  load_balancing_scheme = "INTERNAL_MANAGED"
  timeout_sec           = local.hybrid_timeout_sec
  health_checks         = [google_compute_region_health_check.aiplatform_hybrid[0].id]

  backend {
    group                 = google_compute_network_endpoint_group.aiplatform_hybrid[0].id
    balancing_mode        = "RATE"
    max_rate_per_endpoint = 1000
    capacity_scaler       = 1.0
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }

  depends_on = [google_compute_network_endpoint.aiplatform_hybrid]
}
