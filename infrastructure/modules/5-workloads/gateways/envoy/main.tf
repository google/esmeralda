terraform {
  required_version = ">= 1.3"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

# Envoy gateway on Cloud Run for *.esmeralda.internal.
# The Envoy bootstrap comes from ./config, which tests/gateway renders and runs in a real envoy
# (make test-gateway-unit). Like every module under gateways/, it owns no load balancer, certificate
# or DNS record: the layer-5 `entrypoint` routes hosts to the backend service below.
module "config" {
  source = "./config"

  # Only the attributes the config contract takes (probe is for `make test-gateway`, see outputs.tf).
  agent_endpoints = {
    for key, ep in var.agent_endpoints : key => {
      logical_name = ep.logical_name
      engine_id    = ep.engine_id
      endpoint_url = ep.endpoint_url
      audience     = ep.audience
      path_map     = ep.path_map
      streaming    = ep.streaming
    }
  }
  apex_domain           = var.apex_domain
  rate_limit_per_minute = var.rate_limit_per_minute
}

locals {
  # Cloud Run accepts ID tokens for these audiences, so callers can mint tokens for the hostname they call.
  custom_audiences = flatten([for host in module.config.hostnames : ["https://${host}", "http://${host}", host]])
}

# The Envoy bootstrap, stored in Secret Manager and mounted into the container.
resource "google_secret_manager_secret" "config" {
  secret_id = "envoy-config-${var.environment}"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "config" {
  secret      = google_secret_manager_secret.config.id
  secret_data = module.config.envoy_yaml

  # Each revision mounts the exact version it was deployed with. Create the new version before the service
  # moves to it, and keep old versions readable (ABANDON leaves them in Secret Manager): earlier revisions
  # can still start instances, and traffic can be rolled back to them. Prune old versions out of band.
  deletion_policy = "ABANDON"
  lifecycle {
    create_before_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "gateway_sa_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.gateway_sa_email}"
}

# Envoy on Cloud Run with internal-only ingress. Mounting the exact secret version makes every config
# change roll out as a new revision.
resource "google_cloud_run_v2_service" "gateway" {
  name                = "envoy-gateway-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false
  custom_audiences    = local.custom_audiences

  template {
    service_account = var.gateway_sa_email
    # Also the effective load balancer timeout: serverless NEG backend services take no timeout_sec.
    timeout = var.request_timeout

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image_by_digest
      args  = ["-c", "/etc/envoy/envoy.yaml", "--disable-hot-restart", "--log-level", "info"]
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      volume_mounts {
        name       = "envoy-config"
        mount_path = "/etc/envoy"
      }
    }

    volumes {
      name = "envoy-config"
      secret {
        secret = google_secret_manager_secret.config.secret_id
        items {
          version = google_secret_manager_secret_version.config.version
          path    = "envoy.yaml"
        }
      }
    }

    # Direct VPC egress for all traffic, so calls to internal-ingress Cloud Run upstreams count as internal.
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.gateway_sa_secret_access]
}

resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  members  = [for sa in var.invoker_service_accounts : "serviceAccount:${sa}"]
}

# ====================================================================
# Gateway contract: a regional INTERNAL_MANAGED backend service
# --------------------------------------------------------------------
# The layer-5 `entrypoint` unit owns the URL map, HTTPS proxy and forwarding rule on the layer-2
# reserved IP, and routes hosts to this backend service (output `backend_service_id`).
# ====================================================================

resource "google_compute_region_network_endpoint_group" "gateway" {
  name                  = "neg-envoy-gateway-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.gateway.name
  }
}

# No timeout_sec: it can't be set on serverless NEG backends. The Cloud Run request timeout above
# bounds requests, SSE streams included.
resource "google_compute_region_backend_service" "gateway" {
  name                  = "backend-envoy-gateway-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.gateway.id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}
