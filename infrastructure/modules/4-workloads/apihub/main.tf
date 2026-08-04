# infrastructure/modules/4-workloads/apihub/main.tf

resource "google_project_service_identity" "apihub_service_identity" {
  provider = google-beta
  project  = var.project_id
  service  = "apihub.googleapis.com"
}

resource "google_project_iam_member" "apihub_service_identity_permission" {
  provider = google-beta
  for_each = toset(["roles/apihub.admin", "roles/apihub.runtimeProjectServiceAgent"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_project_service_identity.apihub_service_identity.email}"
}

resource "google_apihub_host_project_registration" "apihub_host_project" {
  provider                     = google-beta
  project                      = var.project_id
  location                     = var.region
  host_project_registration_id = var.project_id
  gcp_project                  = "projects/${var.project_id}"
  depends_on                   = [google_project_iam_member.apihub_service_identity_permission]
}

resource "google_apihub_api_hub_instance" "main" {
  provider            = google-beta
  project             = var.project_id
  location            = var.region
  api_hub_instance_id = var.api_hub_instance_id

  config {
    disable_search  = false
    vertex_location = "us"
  }

  timeouts { create = "35m" }
  depends_on = [google_apihub_host_project_registration.apihub_host_project]
}
