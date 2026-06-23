variable "project_id" {
  description = "The GCP project ID allocated for downstream agents (prj-esmeralda-agents)"
  type        = string
}

variable "region" {
  description = "The region where Cloud SQL and the Reasoning Engine are deployed"
  type        = string
  default     = "us-central1"
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
  description = "The registered display name of the A2A Mortgage Assistant reasoning engine"
  type        = string
  default     = "a2a-mortgage-agent"
}

variable "agent_service_account" {
  description = "The email address of the dedicated A2A Agent service account created in Stage 3"
  type        = string
}

# Cloud SQL Sizing Variables
variable "sql_tier" {
  description = "The machine instance type allocated for the Cloud SQL PostgreSQL task store"
  type        = string
  default     = "db-custom-1-3840" # Lightweight instance type for standard workloads
}

variable "database_name" {
  description = "The name of the task store relational database"
  type        = string
  default     = "a2a_tasks"
}

# Packaging paths for the ADK bundle
variable "pickle_object_path" {
  description = "The local directory path containing the pre-packaged serialized agent.pkl file"
  type        = string
}

variable "requirements_path" {
  description = "The local directory path containing the pre-packaged requirements.txt bundle"
  type        = string
}

variable "dependencies_path" {
  description = "The local directory path containing the pre-packaged dependencies.tar.gz bundle"
  type        = string
}

variable "network_attachment" {
  description = "Optional Private Service Connect Network Attachment ID for Vertex AI Reasoning Engine VPC attachment"
  type        = string
  default     = ""
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}
