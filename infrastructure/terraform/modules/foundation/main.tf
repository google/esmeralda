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

resource "google_project" "project" {
  name              = "${var.project_id}-${var.random_suffix}"
  project_id        = "${var.project_id}-${var.random_suffix}"
  org_id            = var.folder_id == null ? var.org_id : null
  folder_id         = var.folder_id
  billing_account   = var.billing_account
  deletion_policy = "DELETE"
}

resource "google_project_service" "apis" {
  project = google_project.project.project_id
  # Ensure project is created before enabling APIs
  depends_on = [google_project.project]

  for_each                   = toset(var.apis_to_enable)
  service                    = each.key
  disable_dependent_services = true
}

# Ensure Vertex AI service identity exists
resource "google_project_service_identity" "aiplatform" {
  provider = google-beta
  project  = google_project.project.project_id
  service  = "aiplatform.googleapis.com"
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "aiplatform_network_admin" {
  count      = var.enable_psc_interface ? 1 : 0
  project    = google_project.project.project_id
  role       = "roles/compute.networkAdmin"
  member     = "serviceAccount:${google_project_service_identity.aiplatform.email}"
  depends_on = [time_sleep.aiplatform_identity_propagation]
}

resource "google_project_iam_member" "aiplatform_dns_peer" {
  count      = var.enable_psc_interface ? 1 : 0
  project    = google_project.project.project_id
  role       = "roles/dns.peer"
  member     = "serviceAccount:${google_project_service_identity.aiplatform.email}"
  depends_on = [time_sleep.aiplatform_identity_propagation]
}

resource "time_sleep" "aiplatform_identity_propagation" {
  count           = var.enable_psc_interface ? 1 : 0
  depends_on      = [google_project_service_identity.aiplatform]
  create_duration = "30s"
}

resource "google_project_iam_member" "aiplatform_re_network_admin" {
  count      = var.enable_psc_interface ? 1 : 0
  project    = google_project.project.project_id
  role       = "roles/compute.networkAdmin"
  member     = "serviceAccount:service-${google_project.project.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
  depends_on = [time_sleep.aiplatform_identity_propagation]
}

resource "google_project_iam_member" "aiplatform_re_dns_peer" {
  count      = var.enable_psc_interface ? 1 : 0
  project    = google_project.project.project_id
  role       = "roles/dns.peer"
  member     = "serviceAccount:service-${google_project.project.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
  depends_on = [time_sleep.aiplatform_identity_propagation]
}
