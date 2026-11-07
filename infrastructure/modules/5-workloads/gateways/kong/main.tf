terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
  }
}

# Compile the declarative kong.yml file dynamically based on agent_endpoints
locals {
  kong_config = templatefile("${path.module}/templates/kong.yml.tpl", {
    agent_endpoints = var.agent_endpoints
  })

  # Kong status API port (health checks only; distinct from the 8000 proxy listener)
  kong_status_port = 8100
}

# Securely store the compiled Kong declarative configuration inside Secret Manager
resource "google_secret_manager_secret" "kong_config" {
  secret_id = "kong-config-${var.environment}"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "kong_config" {
  secret      = google_secret_manager_secret.kong_config.id
  secret_data = local.kong_config
}



# Resolve pre-created Service Account for Kong from Layer 3 Security
data "google_service_account" "kong_sa" {
  account_id = "sa-esmeralda-kong-${var.environment}"
  project    = var.project_id
}

# Allow Kong Service Account to read declarative configuration from Secret Manager
resource "google_secret_manager_secret_iam_member" "kong_sa_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.kong_config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_service_account.kong_sa.email}"
}

# Deploy Kong Gateway on Cloud Run with internal-only ingress
resource "google_cloud_run_v2_service" "kong_gateway" {
  name                = "kong-gateway-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  custom_audiences = [
    "http://esmeralda.internal",
    "https://esmeralda.internal",
    "esmeralda.internal",
    "http://ai-coe-mortgage-specialist.esmeralda.internal",
    "https://ai-coe-mortgage-specialist.esmeralda.internal",
    "ai-coe-mortgage-specialist.esmeralda.internal",
    "http://cx-mortgage-orchestrator.esmeralda.internal",
    "https://cx-mortgage-orchestrator.esmeralda.internal",
    "cx-mortgage-orchestrator.esmeralda.internal",
    "http://legacy-dms.esmeralda.internal",
    "https://legacy-dms.esmeralda.internal",
    "legacy-dms.esmeralda.internal",
    "http://income-verification.esmeralda.internal",
    "https://income-verification.esmeralda.internal",
    "income-verification.esmeralda.internal",
    "http://corporate-email.esmeralda.internal",
    "https://corporate-email.esmeralda.internal",
    "corporate-email.esmeralda.internal"
  ]

  depends_on = [
    google_secret_manager_secret_iam_member.kong_sa_secret_access,
    google_secret_manager_secret_version.kong_config
  ]

  template {
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    service_account = data.google_service_account.kong_sa.email

    containers {
      image = local.image_by_digest
      ports {
        container_port = 8000
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      env {
        name  = "KONG_DATABASE"
        value = "off"
      }
      env {
        name  = "KONG_DECLARATIVE_CONFIG"
        value = "/etc/kong/kong.yml"
      }
      env {
        name  = "KONG_PLUGINS"
        value = "bundled,gcp-service-account"
      }
      # One nginx worker per vCPU. With more workers than vCPUs (and CPU throttled between
      # requests), workers can fail to reach the shared worker-events socket and stall.
      env {
        name  = "KONG_NGINX_WORKER_PROCESSES"
        value = "1"
      }
      # Status API (read-only health endpoints), used by the liveness probe below.
      # Not exposed through Cloud Run ingress, which only routes to the container port.
      env {
        name  = "KONG_STATUS_LISTEN"
        value = "0.0.0.0:${local.kong_status_port}"
      }
      env {
        name  = "FORCE_REDEPLOY"
        value = google_secret_manager_secret_version.kong_config.version
      }

      # Restart the instance when Kong stops answering. /status/ready (Kong >= 3.3) is served
      # by the nginx worker and returns 200 only once the declarative config, router and
      # plugins are loaded, so a hung worker or a lost config both fail the probe.
      # The default TCP startup probe on the container port still gates the first traffic.
      liveness_probe {
        initial_delay_seconds = 10
        period_seconds        = 15
        timeout_seconds       = 5
        failure_threshold     = 3
        http_get {
          path = "/status/ready"
          port = local.kong_status_port
        }
      }
      volume_mounts {
        name       = "kong-config"
        mount_path = "/etc/kong"
      }
    }

    volumes {
      name = "kong-config"
      secret {
        secret = google_secret_manager_secret.kong_config.secret_id
        items {
          version = "latest"
          path    = "kong.yml"
        }
      }
    }

    # Direct VPC Egress: Mounts Cloud Run inside the Shared VPC directly
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}

# IAM Invoker Binding restricting access to authorized callers only
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.kong_gateway.name
  role     = "roles/run.invoker"
  members = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# ====================================================================
# Gateway contract: a regional INTERNAL_MANAGED backend service
# --------------------------------------------------------------------
# Kong does not own a load balancer, certificate or DNS record. The layer-5 `entrypoint` unit
# owns the URL map, HTTPS proxy and forwarding rule on the layer-2 reserved IP, and routes
# hosts to this backend service (output `backend_service_id`).
# ====================================================================

# Serverless NEG fronting the Kong Gateway Cloud Run service
resource "google_compute_region_network_endpoint_group" "kong_neg" {
  name                  = "neg-kong-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.kong_gateway.name
  }
}

# Regional internal backend service (the gateway contract output)
resource "google_compute_region_backend_service" "kong_backend" {
  name                  = "backend-kong-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.kong_neg.id
  }
}
