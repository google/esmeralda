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
  description = "IAM members granted roles/iap.egressor (layer 4 governance output iap_egress_members)"
  type        = list(string)
}

variable "registry_service_ids" {
  description = "Agent Registry services registered by layer 5 (MCP servers, A2A agents); changes re-trigger the grant"
  type        = list(string)
}
