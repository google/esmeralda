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
    # The route timeout bounds the whole response; the psc backend also has a fixed 30 s time to first byte.
    condition     = alltrue([for agent in values(var.agents) : agent.timeout_sec >= 30 && agent.timeout_sec <= 3600])
    error_message = "agents[*].timeout_sec must be between 30 and 3600 seconds (300 or more for agent streams)."
  }
}

variable "hybrid_agent_backend" {
  description = "Also create the hybrid Agent Runtime backend: a hybrid NEG pointing at the Shared VPC's Private Service Connect endpoint for Google APIs (googleapis_psc_ip:443, layer 2) and a backend service whose timeout is the agents' longest route timeout. Unlike the psc backend, it has no fixed 30 s time to first byte. Creating it does not route traffic to it (see agent_backend)"
  type        = bool
  default     = false
}

variable "agent_backend" {
  description = "Backend of every agent route: psc (PSC NEG to the aiplatform API; fixed 30 s time to first byte, so a unary call such as A2A message:send that answers later gets a 504) or hybrid (needs hybrid_agent_backend)"
  type        = string
  default     = "psc"

  validation {
    condition     = contains(["psc", "hybrid"], var.agent_backend)
    error_message = "agent_backend must be psc or hybrid."
  }
}

variable "network_id" {
  description = "Shared VPC network of the entry point (layer 2). Required by the hybrid backend's NEG"
  type        = string
  default     = null
}

variable "googleapis_psc_ip" {
  description = "Internal IP of the Shared VPC's Private Service Connect endpoint for Google APIs (layer 2 output googleapis_psc_ip). Required by the hybrid backend"
  type        = string
  default     = ""
}

variable "hybrid_neg_zone" {
  description = "Zone of the hybrid NEG (hybrid NEGs are zonal). Empty: <region>-a"
  type        = string
  default     = ""
}
