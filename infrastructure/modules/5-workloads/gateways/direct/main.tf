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
#   - Agents (Agent Runtime): one Private Service Connect NEG targeting the regional aiplatform API
#     + one HTTPS backend service in the gateway project, shared by every agent. Each agent host
#     gets a route that rewrites the host and the path prefix to its engine (see the `hosts`
#     output). Callers send an OAuth2 access token (Agent Runtime rejects ID tokens).
#
# Nothing here swaps or adds credentials, so each service sees the real caller's identity.
# ====================================================================

locals {
  aiplatform_host = "${var.region}-aiplatform.googleapis.com"

  # Engine resource path of each agent: "/v1beta1/projects/<p>/locations/<r>/reasoningEngines/<id>".
  engine_paths = { for name, agent in var.agents : name => replace(agent.endpoint_url, "/^https://[^/]+/", "/") }
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
# timeout. Long agent streams rely on the per-route timeout the entry point sets (hosts output).
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
