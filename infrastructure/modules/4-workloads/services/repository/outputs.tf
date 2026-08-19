output "repository_id" {
  description = "The repository ID of the provisioned Artifact Registry repository"
  value       = var.byo_cicd_project ? "esmeralda-containers" : google_artifact_registry_repository.esmeralda_containers[0].repository_id
}

output "repository_url" {
  description = "The Docker image repository URL prefix"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.byo_cicd_project ? "esmeralda-containers" : google_artifact_registry_repository.esmeralda_containers[0].repository_id}"
}
