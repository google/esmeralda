terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
  }
}

resource "google_artifact_registry_repository" "esmeralda_containers" {
  location      = var.region
  repository_id = "esmeralda-containers"
  description   = "Unified Docker container repository for Esmeralda microservices and AI agents"
  format        = "DOCKER"
  project       = var.project_id
}

