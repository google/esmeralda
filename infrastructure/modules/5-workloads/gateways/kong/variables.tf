# --- INPUT VARIABLES CONTRACT ---
variable "project_id" {
  description = "The GCP project ID allocated for gateway ingress (prj-esmeralda-gateway)"
  type        = string
}

variable "region" {
  description = "The region where gateway workloads are deployed"
  type        = string
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network (Kong's Direct VPC egress)"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the subnetwork used for Kong's Direct VPC egress"
  type        = string
}

variable "agent_endpoints" {
  description = "A map of logical agent names to their dynamic endpoints and routing configuration"
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
    audience     = string
    # How `make test-gateway` checks the route: "mcp" (MCP initialize, POST /mcp),
    # "a2a" (agent card, GET /v1/card) or "none" (TLS + routing only, any HTTP answer).
    probe = optional(string, "none")
  }))
  default = {}

  validation {
    condition     = alltrue([for e in values(var.agent_endpoints) : contains(["mcp", "a2a", "none"], e.probe)])
    error_message = "agent_endpoints[*].probe must be one of: mcp, a2a, none."
  }
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "kong_image" {
  description = "The Container Image URL of Kong Gateway"
  type        = string
  default     = "kong:latest"
}

variable "invoker_service_accounts" {
  description = "A list of service accounts allowed to invoke the Kong gateway"
  type        = list(string)
  default     = []
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
