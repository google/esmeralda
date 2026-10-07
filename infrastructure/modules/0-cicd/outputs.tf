# infrastructure/modules/0-cicd/outputs.tf

output "cicd_project_id" {
  description = "Shared CI/CD project ID (Cloud Build + Artifact Registry)."
  value       = google_project.cicd.project_id
}

output "cicd_project_number" {
  description = "Shared CI/CD project number."
  value       = google_project.cicd.number
}

output "region" {
  description = "Region of the Artifact Registry repositories and Cloud Build."
  value       = var.region
}

output "dev_repository_id" {
  description = "Dev Artifact Registry repository ID (mutable tags)."
  value       = google_artifact_registry_repository.dev.repository_id
}

output "dev_repository_url" {
  description = "Docker URL prefix of the dev repository."
  value       = "${var.region}-docker.pkg.dev/${google_project.cicd.project_id}/${google_artifact_registry_repository.dev.repository_id}"
}

output "release_repository_id" {
  description = "Release Artifact Registry repository ID (immutable tags)."
  value       = google_artifact_registry_repository.release.repository_id
}

output "release_repository_url" {
  description = "Docker URL prefix of the release repository."
  value       = "${var.region}-docker.pkg.dev/${google_project.cicd.project_id}/${google_artifact_registry_repository.release.repository_id}"
}

output "builder_sa_email" {
  description = "Env-neutral Cloud Build builder service account."
  value       = google_service_account.builder.email
}

output "promoter_sa_email" {
  description = "Release promoter service account (reads dev, writes release)."
  value       = google_service_account.promoter.email
}

output "cloudbuild_service_agent" {
  description = "Cloud Build service agent of the shared CI/CD project."
  value       = google_project_service_identity.cloudbuild.email
}
