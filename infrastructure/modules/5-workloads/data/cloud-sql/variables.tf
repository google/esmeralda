variable "enabled" {
  description = "Whether to provision the Cloud SQL PostgreSQL task store instance"
  type        = bool
  default     = true
}

variable "project_id" {
  description = "The GCP project ID allocated for downstream agents (prj-esmeralda-agents)"
  type        = string
}

variable "region" {
  description = "The region where Cloud SQL is deployed"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the backend workload subnet inside the Shared VPC"
  type        = string
}

variable "agent_name" {
  description = "The workload name prefix for the Cloud SQL instance and bootstrap job"
  type        = string
  default     = "ai-coe-mortgage-specialist"
}

variable "agent_service_account" {
  description = "The email address of the dedicated A2A Agent service account created in Layer 3"
  type        = string
}

variable "sql_tier" {
  description = "The machine instance type allocated for the Cloud SQL PostgreSQL task store"
  type        = string
  default     = "db-custom-1-3840"
}

variable "database_name" {
  description = "The name of the task store relational database"
  type        = string
  default     = "a2a_tasks"
}

variable "database_key_id" {
  description = "Optional KMS crypto key ID from Layer 3 for Cloud SQL CMEK encryption"
  type        = string
  default     = null
}

variable "admin_password_secret_id" {
  description = "Optional Secret Manager secret ID from Layer 3 storing DB admin credentials"
  type        = string
  default     = null
}
