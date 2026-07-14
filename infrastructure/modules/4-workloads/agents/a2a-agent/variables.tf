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

variable "net_host_project_id" {
  description = "The host project ID hosting the central Shared VPC and Cloud DNS"
  type        = string
  default     = ""
}

variable "vpc_name" {
  description = "The name of the Shared VPC network"
  type        = string
  default     = ""
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

# BYOC Container Image URI
variable "agent_image_uri" {
  description = "The Artifact Registry URI of the pre-built BYOC container image for the agent"
  type        = string
}


variable "network_attachment" {
  description = "Optional Private Service Connect Network Attachment ID for Vertex AI Reasoning Engine VPC attachment"
  type        = string
  default     = ""
}

variable "psc_subnet_id" {
  description = "The self-link of the PSC interface subnetwork inside the Shared VPC"
  type        = string
  default     = ""
}

variable "enable_psc_network" {
  description = "Whether to create a per-agent PSC Network Attachment and enable PSC-Interface"
  type        = bool
  default     = true
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}

variable "agent_config_path" {
  description = "Absolute path to the agent.yaml file defining agent resources, metadata, and environment variables"
  type        = string
  default     = ""
}

variable "agent_card_json" {
  description = "The JSON string of the exported A2A agent card"
  type        = string
  default     = ""
}

