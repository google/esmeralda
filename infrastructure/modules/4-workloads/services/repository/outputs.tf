output "repository_id" {
  description = "The repository ID of the provisioned Artifact Registry repository"
  value       = google_artifact_registry_repository.esmeralda_containers.repository_id
}

output "repository_url" {
  description = "The Docker image repository URL prefix"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.esmeralda_containers.repository_id}"
}
