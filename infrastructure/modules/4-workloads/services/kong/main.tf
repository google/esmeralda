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
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false

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
