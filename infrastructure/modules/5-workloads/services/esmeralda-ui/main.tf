# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.0.0"
    }
  }
}

data "google_project" "service_project" {
  project_id = var.project_id
}

locals {
  service_name              = "esmeralda-ui"
  runtime_catalog_secret_id = var.runtime_catalog_secret_id != "" ? var.runtime_catalog_secret_id : "esmeralda-runtime-catalog-${var.environment}"
  gcip_enabled              = var.iap_enabled && try(var.identity_platform.enabled, false)

  image_input      = var.container_image
  image_resolvable = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/[^/]+/[^/]+/[^/@]+:[^/@]+$", local.image_input))
  image_parts      = split("/", local.image_input)
  image_name_tag   = local.image_resolvable ? local.image_parts[3] : ""
}

data "google_artifact_registry_docker_image" "image" {
  count         = local.image_resolvable ? 1 : 0
  project       = local.image_parts[1]
  location      = replace(local.image_parts[0], "-docker.pkg.dev", "")
  repository_id = local.image_parts[2]
  image_name    = local.image_name_tag
}

locals {
  image_by_digest = local.image_resolvable ? "${join("/", slice(local.image_parts, 0, 3))}/${split(":", local.image_name_tag)[0]}@${split("@", data.google_artifact_registry_docker_image.image[0].name)[1]}" : local.image_input
}

resource "google_service_account" "ui_sa" {
  provider     = google
  project      = var.project_id
  account_id   = "sa-esmeralda-ui-${var.environment}"
  display_name = "Esmeralda UI BFF Service Account (${var.environment})"
}

# Read bound runtime catalog from Governance Secret Manager
resource "google_secret_manager_secret_iam_member" "runtime_catalog_accessor" {
  project   = var.governance_project_id
  secret_id = local.runtime_catalog_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ui_sa.email}"
}

# Read usage records and traces in the Governance project
resource "google_project_iam_member" "governance_logging_viewer" {
  project = var.governance_project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.ui_sa.email}"
}

resource "google_project_iam_member" "governance_bigquery_viewer" {
  project = var.governance_project_id
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.ui_sa.email}"
}

# Provision the IAP Service Agent so Direct Cloud Run IAP can invoke the service under Domain Restricted Sharing
resource "google_project_service_identity" "iap_sa" {
  count    = var.iap_enabled ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"
}

resource "google_cloud_run_v2_service" "esmeralda_ui" {
  provider             = google-beta
  name                 = local.service_name
  location             = var.region
  project              = var.project_id
  ingress              = var.iap_enabled ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  iap_enabled          = var.iap_enabled
  deletion_protection  = false

  template {
    service_account = google_service_account.ui_sa.email

    vpc_access {
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    dynamic "volumes" {
      for_each = var.root_ca_secret_id != "" ? [1] : []
      content {
        name = "internal-ca"
        secret {
          secret = var.root_ca_secret_id
          items {
            version = "latest"
            path    = "esmeralda-ca.pem"
          }
        }
      }
    }

    containers {
      image = local.image_by_digest

      ports {
        container_port = 8080
      }

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "ENTRYPOINT_DOMAIN"
        value = var.entrypoint_domain
      }
      env {
        name  = "GOVERNANCE_PROJECT_ID"
        value = var.governance_project_id
      }
      env {
        name  = "RUNTIME_CATALOG_SECRET_ID"
        value = local.runtime_catalog_secret_id
      }
      env {
        name  = "ESMERALDA_UI_ALLOW_PERSONA_OVERRIDE"
        value = tostring(var.allow_persona_override)
      }

      dynamic "env" {
        for_each = var.root_ca_secret_id != "" ? [1] : []
        content {
          name  = "NODE_EXTRA_CA_CERTS"
          value = "/etc/ssl/certs/internal-ca/esmeralda-ca.pem"
        }
      }

      dynamic "volume_mounts" {
        for_each = var.root_ca_secret_id != "" ? [1] : []
        content {
          name       = "internal-ca"
          mount_path = "/etc/ssl/certs/internal-ca"
        }
      }
    }
  }
}

# Grant roles/run.invoker to the IAP Service Agent (allowed by Domain Restricted Sharing) + explicit invoker_members
resource "google_cloud_run_v2_service_iam_member" "iap_service_agent_invoker" {
  count    = var.iap_enabled ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.esmeralda_ui.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.service_project.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

resource "google_cloud_run_v2_service_iam_member" "explicit_invokers" {
  for_each = toset(var.invoker_members)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.esmeralda_ui.name
  role     = "roles/run.invoker"
  member   = each.value
}

# Path 1: Google Group / User IAP accessors
resource "google_iap_web_cloud_run_service_iam_member" "iap_accessors" {
  for_each               = var.iap_enabled ? toset(var.iap_accessors) : toset([])
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.esmeralda_ui.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

# Path 2: Optional Identity Platform (GCIP) integration on Direct Cloud Run IAP
resource "google_iap_settings" "gcip_settings" {
  count    = local.gcip_enabled ? 1 : 0
  provider = google-beta
  name     = "projects/${data.google_project.service_project.number}/iap_web/cloud_run-${var.region}/services/${google_cloud_run_v2_service.esmeralda_ui.name}"

  access_settings {
    gcip_settings {
      tenant_ids     = var.identity_platform.tenant_ids
      login_page_uri = var.identity_platform.login_page_uri != "" ? var.identity_platform.login_page_uri : null
    }
  }
}
