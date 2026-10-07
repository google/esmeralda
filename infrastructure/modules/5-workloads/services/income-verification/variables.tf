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
  description = "The GCR/Artifact Registry container image URI for income-verification"
  type        = string
}

variable "invoker_service_accounts" {
  description = "The list of service account emails authorized to invoke this MCP server"
  type        = list(string)
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
  description = "Internal hostname served by Kong for this MCP server"
  type        = string
  default     = "income-verification.esmeralda.internal"
}
