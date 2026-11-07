# --- INPUT VARIABLES CONTRACT ---

variable "project_id" {
  description = "Gateway project: hosts the entry point and the shared Agent Runtime (aiplatform) backend"
  type        = string
}

variable "region" {
  description = "Region of the entry point (regional internal Application Load Balancer) and of every backend"
  type        = string
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, prd)"
  type        = string
  default     = "dev"
}

variable "mcp_services" {
  description = "Catalog entries of kind mcp: name => Cloud Run service (its project and service name). The NEG and backend service are created in that project"
  type = map(object({
    project_id   = string
    service_name = string
  }))
  default = {}
}

variable "agents" {
  description = "Catalog entries of kind a2a or adk: name => kind, engine endpoint URL (https://<region>-aiplatform.googleapis.com/v1beta1/projects/<p>/locations/<r>/reasoningEngines/<id>) and route timeout"
  type = map(object({
    kind         = string
    endpoint_url = string
    timeout_sec  = optional(number, 600)
  }))
  default = {}

  validation {
    condition     = alltrue([for agent in values(var.agents) : contains(["a2a", "adk"], agent.kind)])
    error_message = "agents[*].kind must be a2a or adk."
  }

  validation {
    condition     = alltrue([for agent in values(var.agents) : can(regex("^https://${var.region}-aiplatform\\.googleapis\\.com/v1(beta1)?/projects/[^/]+/locations/[^/]+/reasoningEngines/[^/]+$", agent.endpoint_url))])
    error_message = "agents[*].endpoint_url must be the engine URL in the entry point region: https://<region>-aiplatform.googleapis.com/v1beta1/projects/<p>/locations/<r>/reasoningEngines/<id>."
  }

  validation {
    # The backend service timeout is fixed at 30 s for this backend: agent streams need a route timeout.
    condition     = alltrue([for agent in values(var.agents) : agent.timeout_sec >= 30 && agent.timeout_sec <= 3600])
    error_message = "agents[*].timeout_sec must be between 30 and 3600 seconds (300 or more for agent streams)."
  }
}
