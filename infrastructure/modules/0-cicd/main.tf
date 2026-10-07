# infrastructure/modules/0-cicd/main.tf
# ====================================================================
# LAYER 0: SHARED CI/CD (env-independent, never destroyed by env teardown)
# One project, one Cloud Build, two Artifact Registry repositories:
#   - dev repo (mutable tags):      every build lands here (dev-latest, dev-<sha>)
#   - release repo (immutable tags): images promoted by digest (vX.Y.Z)
# Environments only add consumer-side, repository-level reader IAM.
# ====================================================================

resource "random_id" "project_suffix" {
  byte_length = 2
}

locals {
  project_id = "${var.project_prefix}-cicd-${random_id.project_suffix.hex}"

  cicd_apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project" "cicd" {
  name                = local.project_id
  project_id          = local.project_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = false
  # Shared by every environment and holds release images: never delete by accident.
  deletion_policy = "PREVENT"

  labels = {
    "managed-by"  = "terragrunt-esmeralda"
    "env"         = "shared"
    "cost-center" = "shared-cicd-and-artifacts"
    "team"        = "platform-engineering"
  }
}

# The provider runs with user_project_override, so the new project must have
# serviceusage enabled before Terraform can enable anything else on it.
resource "null_resource" "serviceusage_bootstrap" {
  triggers = {
    project_id = google_project.cicd.project_id
  }

  provisioner "local-exec" {
    command = <<EOT
      gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com --project="${google_project.cicd.project_id}"
      sleep 15
    EOT
  }
}

resource "google_project_service" "cicd" {
  for_each                   = toset(local.cicd_apis)
  project                    = google_project.cicd.project_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [null_resource.serviceusage_bootstrap]
}

resource "time_sleep" "api_propagation" {
  create_duration = "30s"
  depends_on      = [google_project_service.cicd]
}

resource "google_project_service_identity" "cloudbuild" {
  provider = google-beta
  project  = google_project.cicd.project_id
  service  = "cloudbuild.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# --------------------------------------------------------------------
# Artifact Registry repositories
# --------------------------------------------------------------------

resource "google_artifact_registry_repository" "dev" {
  project       = google_project.cicd.project_id
  location      = var.region
  repository_id = var.dev_repository_id
  description   = "Esmeralda dev builds (mutable tags: dev-latest, dev-<sha>)"
  format        = "DOCKER"

  docker_config {
    immutable_tags = false
  }

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "delete-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = var.untagged_image_retention
    }
  }

  depends_on = [time_sleep.api_propagation]
}

resource "google_artifact_registry_repository" "release" {
  project       = google_project.cicd.project_id
  location      = var.region
  repository_id = var.release_repository_id
  description   = "Esmeralda promoted releases (immutable tags: vX.Y.Z, copied by digest from the dev repo)"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }

  depends_on = [time_sleep.api_propagation]
}

# --------------------------------------------------------------------
# Builder identity: builds any image, writes only to the dev repository
# --------------------------------------------------------------------

resource "google_service_account" "builder" {
  project      = google_project.cicd.project_id
  account_id   = "sa-esmeralda-builder"
  display_name = "Esmeralda env-neutral Cloud Build image builder"

  depends_on = [time_sleep.api_propagation]
}

resource "google_project_iam_member" "builder_roles" {
  for_each = toset([
    "roles/cloudbuild.builds.builder",
    "roles/storage.admin", # regional user-owned Cloud Build source bucket
    "roles/logging.logWriter",
  ])
  project = google_project.cicd.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.builder.email}"
}

resource "google_artifact_registry_repository_iam_member" "builder_dev_writer" {
  project    = google_project.cicd.project_id
  location   = var.region
  repository = google_artifact_registry_repository.dev.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.builder.email}"
}

# --------------------------------------------------------------------
# Promoter identity: reads dev, writes release (copy by digest)
# --------------------------------------------------------------------

resource "google_service_account" "promoter" {
  project      = google_project.cicd.project_id
  account_id   = "sa-esmeralda-promoter"
  display_name = "Esmeralda release promoter (dev repo -> release repo)"

  depends_on = [time_sleep.api_propagation]
}

resource "google_artifact_registry_repository_iam_member" "promoter_dev_reader" {
  project    = google_project.cicd.project_id
  location   = var.region
  repository = google_artifact_registry_repository.dev.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.promoter.email}"
}

resource "google_artifact_registry_repository_iam_member" "promoter_release_writer" {
  project    = google_project.cicd.project_id
  location   = var.region
  repository = google_artifact_registry_repository.release.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.promoter.email}"
}
