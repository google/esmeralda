variable "project_id" {
  description = "The GCP Project ID where the Artifact Registry repository will be created"
  type        = string
}

variable "region" {
  description = "The GCP region for the Artifact Registry repository"
  type        = string
}

variable "byo_cicd_project" {
  description = "Whether the CI/CD project is BYO/shared so Artifact Registry repository already exists"
  type        = bool
  default     = false
}
