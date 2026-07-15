# Compile the declarative kong.yml file dynamically based on agent_endpoints
locals {
  kong_config = templatefile("${path.module}/templates/kong.yml.tpl", {
    agent_endpoints = var.agent_endpoints
  })
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

import {
  id = "projects/esmeralda-gateway-918f/serviceAccounts/kong-gateway-sa-dev@esmeralda-gateway-918f.iam.gserviceaccount.com"
  to = google_service_account.kong_sa
}

# Create a dedicated Cloud Run Service Account for Kong
resource "google_service_account" "kong_sa" {
  account_id   = "kong-gateway-sa-${var.environment}"
  display_name = "Kong Gateway Service Account"
  project      = var.project_id
}

# Grant the Service Account permissions to fetch tokens and call Vertex AI Reasoning Engines
resource "google_project_iam_member" "kong_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.kong_sa.email}"
}

# Allow Kong Service Account to read declarative configuration from Secret Manager
resource "google_secret_manager_secret_iam_member" "kong_sa_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.kong_config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.kong_sa.email}"
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
    "http://a2a-mortgage-agent.esmeralda.internal",
    "https://a2a-mortgage-agent.esmeralda.internal",
    "a2a-mortgage-agent.esmeralda.internal",
    "http://root-agent.esmeralda.internal",
    "https://root-agent.esmeralda.internal",
    "root-agent.esmeralda.internal",
    "http://legacy-dms.esmeralda.internal",
    "https://legacy-dms.esmeralda.internal",
    "http://income-verification.esmeralda.internal",
    "https://income-verification.esmeralda.internal",
    "http://corporate-email.esmeralda.internal",
    "https://corporate-email.esmeralda.internal"
  ]

  depends_on = [
    google_secret_manager_secret_iam_member.kong_sa_secret_access,
    google_secret_manager_secret_version.kong_config
  ]

  template {
    service_account = google_service_account.kong_sa.email

    containers {
      image = var.kong_image
      ports {
        container_port = 8000
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
      env {
        name  = "FORCE_REDEPLOY"
        value = google_secret_manager_secret_version.kong_config.version
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
  members  = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# ====================================================================
# Internal HTTP Load Balancer & Cloud DNS Integration for esmeralda.internal
# ====================================================================

# 1. Serverless NEG fronting the Kong Gateway Cloud Run service
resource "google_compute_region_network_endpoint_group" "kong_neg" {
  name                  = "neg-kong-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.kong_gateway.name
  }
}

# 2. Regional Internal Backend Service
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

# 3. Regional Internal URL Map
resource "google_compute_region_url_map" "kong_url_map" {
  name            = "ilb-kong-url-map-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.kong_backend.id
}

# 4. Target HTTP Proxy and Internal Forwarding Rule
resource "google_compute_region_target_http_proxy" "kong_proxy" {
  name    = "ilb-kong-proxy-${var.environment}"
  project = var.project_id
  region  = var.region
  url_map = google_compute_region_url_map.kong_url_map.id
}

resource "google_compute_forwarding_rule" "kong_forwarding_rule" {
  name                  = "ilb-kong-rule-${var.environment}"
  project               = var.project_id
  region                = var.region
  ip_protocol           = "TCP"
  port_range            = "80"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = var.vpc_id
  subnetwork            = var.subnet_id
  target                = google_compute_region_target_http_proxy.kong_proxy.id
}

# 5. Cloud DNS A Records in Shared VPC Private Zone mapping esmeralda.internal to ILB VIP
resource "google_dns_record_set" "esmeralda_internal_apex" {
  count        = var.net_host_project_id != "" && var.dns_zone_name != "" ? 1 : 0
  project      = var.net_host_project_id
  managed_zone = var.dns_zone_name
  name         = "esmeralda.internal."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_forwarding_rule.kong_forwarding_rule.ip_address]
}

resource "google_dns_record_set" "esmeralda_internal_wildcard" {
  count        = var.net_host_project_id != "" && var.dns_zone_name != "" ? 1 : 0
  project      = var.net_host_project_id
  managed_zone = var.dns_zone_name
  name         = "*.esmeralda.internal."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_forwarding_rule.kong_forwarding_rule.ip_address]
}
