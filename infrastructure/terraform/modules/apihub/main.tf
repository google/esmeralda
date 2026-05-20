# Copyright 2025 Google LLC
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

# Create a service identity for API Hub
resource "google_project_service_identity" "apihub_service_identity" {
  provider = google-beta
  project  = var.project_id
  service  = "apihub.googleapis.com"
}

# Grant necessary IAM roles to the API Hub service identity
resource "google_project_iam_member" "apihub_service_identity_permission" {
  provider = google-beta
  for_each = toset([
    "roles/apihub.admin",
    "roles/apihub.runtimeProjectServiceAgent"
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_project_service_identity.apihub_service_identity.email}"
}

# Register the host project for API Hub
resource "google_apihub_host_project_registration" "apihub_host_project" {
  provider = google-beta

  project                     = var.project_id
  location                    = var.region
  host_project_registration_id = var.project_id
  gcp_project                 = "projects/${var.project_id}"

  depends_on = [
    google_project_iam_member.apihub_service_identity_permission
  ]
}

# Provision the API Hub instance
resource "google_apihub_api_hub_instance" "main" {
  provider = google-beta

  project             = var.project_id
  location            = var.region
  api_hub_instance_id = var.api_hub_instance_id
  
  config {
    disable_search = false
    vertex_location = "us"
  }

  timeouts {
    create = "35m"
  }

  depends_on = [
    google_apihub_host_project_registration.apihub_host_project
  ]
}
