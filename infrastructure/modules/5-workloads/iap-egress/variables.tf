# infrastructure/modules/5-workloads/services/iap-egress/variables.tf

variable "governance_project_id" {
  description = "Central governance project hosting the Agent Registry"
  type        = string
}

variable "region" {
  description = "Agent Registry location"
  type        = string
}

variable "iap_egress_members" {
  description = "IAM members granted roles/iap.egressor on the central Agent Registry root (for system endpoints)"
  type        = list(string)
}

variable "registry_service_ids" {
  description = "Agent Registry services registered by layer 5 (MCP servers, A2A agents); changes re-trigger the grant"
  type        = list(string)
}

variable "target_iap_members" {
  description = "Per-target IAM members granted roles/iap.egressor on specific Agent Registry MCP servers and A2A agents"
  type = object({
    mcp_servers = optional(map(list(string)), {})
    agents      = optional(map(list(string)), {})
  })
  default = {}
}
