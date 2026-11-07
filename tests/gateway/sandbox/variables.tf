variable "billing_account" {
  description = "Billing account the sandbox project is linked to"
  type        = string
}

variable "folder_id" {
  description = "Numeric ID of the folder that holds the sandbox project"
  type        = string
}

variable "region" {
  description = "Region for every regional resource"
  type        = string
  default     = "us-central1"
}

variable "project_prefix" {
  description = "Project ID prefix; a random suffix is appended"
  type        = string
  default     = "esm-gw-sbx"
}

variable "gateway_image" {
  description = "Envoy image for the gateway; empty means the one apps/services/envoy/Dockerfile pins, by digest"
  type        = string
  default     = ""
}

variable "upstream_image" {
  description = "Image that runs upstream.py (python:3.12-slim, pinned by digest)"
  type        = string
  default     = "docker.io/library/python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54"
}

variable "rate_limit_per_minute" {
  description = "Per-key rate limit in the sandbox (low, so checks.sh can reach it quickly)"
  type        = number
  default     = 20
}
