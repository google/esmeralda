# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

resource "google_service_account" "cloudbuild_sa" {
  project      = var.project_id
  account_id   = "cloud-build-sa"
  display_name = "Cloud Build Service Account"
}

resource "google_project_iam_member" "cloudbuild_sa_roles" {
  project = var.project_id
  for_each = toset([
    "roles/cloudbuild.builds.builder",
    "roles/storage.admin",
    "roles/logging.logWriter",
    "roles/artifactregistry.reader",
    "roles/artifactregistry.writer",
    "roles/aiplatform.user",
    "roles/iam.serviceAccountUser",
    "roles/aiplatform.serviceAgent",
    "roles/run.admin",
    "roles/apihub.admin"
  ])
  role   = each.key
  member = "serviceAccount:${google_service_account.cloudbuild_sa.email}"
}

resource "google_storage_bucket" "cloudbuild" {
  project                     = var.project_id
  name                        = "${var.project_id}-cloudbuild-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "cloudbuild_compute_sa" {
  bucket = google_storage_bucket.cloudbuild.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.project_number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${var.project_number}-compute@developer.gserviceaccount.com"
}

resource "google_storage_bucket_iam_member" "cloudbuild_service_agent" {
  bucket = google_storage_bucket.cloudbuild.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:service-${var.project_number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
}

resource "google_artifact_registry_repository" "agent_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.agent_name}-repo"
  description   = "Docker repository for the ADK agent"
  format        = "DOCKER"
}

resource "google_artifact_registry_repository" "mcp_repo" {
  project       = var.project_id
  location      = "us-central1"
  repository_id = "remote-mcp-servers"
  description   = "Repository for remote MCP servers"
  format        = "DOCKER"
}
