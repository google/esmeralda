# infrastructure/modules/5-workloads/iap-egress/variables.tf

variable "governance_project_id" {
  description = "Central governance project hosting the Agent Registry"
  type        = string
}

variable "region" {
  description = "Agent Registry location"
  type        = string
}

variable "iap_egress_members" {
  description = "IAM members of the registry-root roles/iap.egressor binding, conditioned to system endpoint entries only (same list as layer 4)"
  type        = list(string)
}

variable "registry_service_ids" {
  description = "Agent Registry services registered by layer 5 (MCP servers, A2A agents); changes re-trigger the grant"
  type        = list(string)
}

variable "target_iap_members" {
  description = "Per-target IAM members granted roles/iap.egressor on specific Agent Registry MCP servers and A2A agents. Members may be `<unit>:<output>` references (the catalog-compiled lists in catalog/.resolved/<env>.json `iap_egress`), resolved through identity_refs"
  type = object({
    mcp_servers = optional(map(list(string)), {})
    agents      = optional(map(list(string)), {})
  })
  default = {}
}

variable "identity_refs" {
  description = "Value of each `<unit>:<output>` reference target_iap_members may use (e.g. layer-3-security:test_vm_sa_email => the test VM SA email, agents/<name>:agent_identity_principal => its principal://). A reference without a value fails the plan; members that are not references pass through unchanged"
  type        = map(string)
  default     = {}
}
