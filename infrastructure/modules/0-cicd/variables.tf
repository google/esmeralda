# infrastructure/modules/0-cicd/variables.tf

variable "project_prefix" {
  description = "Prefix for the shared CI/CD project ID (the project ID is <prefix>-cicd-<random suffix>)."
  type        = string
}

variable "region" {
  description = "Region for the Artifact Registry repositories and Cloud Build."
  type        = string
}

variable "billing_account" {
  description = "Billing account attached to the shared CI/CD project."
  type        = string
}

variable "org_id" {
  description = "Organization ID that parents the project (used when folder_id is empty)."
  type        = string
  default     = ""
}

variable "folder_id" {
  description = "Folder ID that parents the project (takes precedence over org_id)."
  type        = string
  default     = ""
}

variable "dev_repository_id" {
  description = "Artifact Registry repository for dev builds (mutable tags)."
  type        = string
  default     = "esmeralda-containers"
}

variable "release_repository_id" {
  description = "Artifact Registry repository for promoted releases (immutable tags)."
  type        = string
  default     = "esmeralda-containers-release"
}

variable "untagged_image_retention" {
  description = "Untagged images older than this in the dev repository are deleted by the cleanup policy."
  type        = string
  default     = "1209600s" # 14 days
}
