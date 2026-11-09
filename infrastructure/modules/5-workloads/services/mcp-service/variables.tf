variable "service_name" {
  description = "Short canonical identifier of the MCP service (e.g. corporate-email, income-verification, legacy-dms)"
  type        = string
}

variable "display_name" {
  description = "Human-readable display name for logs and service account descriptions"
  type        = string
  default     = ""
}

variable "description" {
  description = "Description published on the central governance Agent Registry MCP entry"
  type        = string
}

variable "register_server_name" {
  description = "Optional override for the server_name passed to apps/services/register_mcp.py (defaults to service_name)"
  type        = string
  default     = ""
}

variable "project_id" {
  description = "The GCP project ID allocated for remote MCP servers (prj-esmeralda-mcps)"
  type        = string
}

variable "region" {
  description = "The region where the Cloud Run service will be deployed"
  type        = string
  default     = "us-central1"
}

variable "network_id" {
  description = "The self-link of the Shared VPC"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the backend subnetwork inside the Shared VPC"
  type        = string
}

variable "container_image" {
  description = "The GCR/Artifact Registry container image URI for the MCP service"
  type        = string
}

variable "invoker_service_accounts" {
  description = "The list of service account emails authorized to invoke this MCP server"
  type        = list(string)
}

variable "service_account_email" {
  description = "Optional workload service account email for the Cloud Run service (uses project default when null)"
  type        = string
  default     = null
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}

variable "min_instances" {
  description = "Minimum number of warm instances for zero cold-start latency"
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Maximum number of instances for auto-scaling"
  type        = number
  default     = 10
}

variable "cpu_limit" {
  description = "CPU resource limit allocated to the container"
  type        = string
  default     = "1"
}

variable "memory_limit" {
  description = "Memory resource limit allocated to the container"
  type        = string
  default     = "512Mi"
}

variable "governance_project_id" {
  description = "Central governance project hosting the Agent Registry the Agent Gateway authorizes egress against"
  type        = string
}

variable "tools_spec_path" {
  description = "Path to the MCP tools.json (TOOL_SPEC) published in the central Agent Registry"
  type        = string
}

variable "internal_hostname" {
  description = "Entry point hostname of this MCP server (<name>.<domain>), published in the Agent Registry"
  type        = string
}

variable "custom_audiences" {
  description = "Optional explicit list of custom OIDC audiences accepted by the Cloud Run service"
  type        = list(string)
  default     = []
}

variable "extra_env_vars" {
  description = "Optional extra plaintext environment variables injected into the Cloud Run container"
  type        = map(string)
  default     = {}
}

variable "secret_env_vars" {
  description = "Optional Secret Manager environment variable references injected into the Cloud Run container"
  type = map(object({
    secret  = string
    version = optional(string, "latest")
  }))
  default = {}
}
