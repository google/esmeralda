# infrastructure/modules/5-workloads/iap-egress/variables.tf

variable "governance_project_id" {
  description = "Central governance project hosting the Agent Registry"
  type        = string
}

variable "region" {
  description = "Agent Registry location"
  type        = string
}

variable "registry_service_ids" {
  description = "Agent Registry services registered by layer 5 (MCP servers, A2A agents); changes re-trigger the grant"
  type        = list(string)
}

variable "target_iap_members" {
  description = "Per-target IAM members granted roles/iap.egressor on specific Agent Registry agents, external endpoints and MCP servers. Members may be `<unit>:<output>` references (the catalog-compiled lists in catalog/.resolved/<env>.json `iap_egress`), resolved through identity_refs. An empty list writes an empty policy (nobody)"
  type = object({
    mcp_servers = optional(map(list(string)), {})
    agents      = optional(map(list(string)), {})
    endpoints   = optional(map(list(string)), {})
  })
  default = {}
}

variable "external_targets" {
  description = "Agent Registry entries of the catalog's managed: false targets (catalog/.resolved/<env>.json `external_targets`), keyed by target name (the entry's service_id). `url` may hold the {domain} and {region} placeholders, substituted here"
  type = map(object({
    kind             = string
    registry         = string
    url              = string
    protocol_binding = string
    description      = string
    owner_team       = optional(string, "")
  }))
  default = {}

  validation {
    condition     = alltrue([for t in values(var.external_targets) : contains(["endpoint", "mcp_server", "agent"], t.registry)])
    error_message = "external_targets[*].registry must be endpoint, mcp_server or agent."
  }
  validation {
    condition     = alltrue([for t in values(var.external_targets) : startswith(t.url, "https://")])
    error_message = "external_targets[*].url must be an https URL."
  }
}

variable "domain" {
  description = "Private domain served by the entry point (layer 2 entrypoint_domain), substituted for {domain} in external_targets URLs"
  type        = string
  default     = ""
}

variable "identity_refs" {
  description = "Value of each `<unit>:<output>` reference target_iap_members may use (e.g. layer-3-security:test_vm_sa_email => the test VM SA email, agents/<name>:agent_identity_principal => its principal://). A reference without a value fails the plan; members that are not references pass through unchanged"
  type        = map(string)
  default     = {}
}

# Verified attribution (attribution.tf)

variable "environment" {
  description = "Environment name (alert display names)"
  type        = string
  default     = ""
}

variable "attribution_enabled" {
  description = "Write the catalog principal_teams rows into principal_teams_table and count gateway decisions for principals without a row"
  type        = bool
  default     = false
}

variable "principal_teams_rows" {
  description = "Catalog-compiled principal -> team rows (catalog/.resolved/<env>.json attribution.principal_teams); principals may be `<unit>:<output>` references resolved through identity_refs. Extra attributes (caller, cost_centers) are ignored"
  type = list(object({
    principal      = string
    principal_type = string
    team           = string
  }))
  default = []
}

variable "principal_teams_table" {
  description = "Fully qualified layer-4 principal_teams table (<project>.<dataset>.principal_teams)"
  type        = string
  default     = ""
}

variable "bigquery_location" {
  description = "Location of the governance BigQuery dataset (where the load job runs)"
  type        = string
  default     = "US"
}

variable "notification_channels" {
  description = "Notification channel names for the gateway attribution alert (layer 4 alert_notification_channels)"
  type        = list(string)
  default     = []
}

variable "alert_on_unattributed" {
  description = "Create the gateway attribution alert (follows layer 4 alert_on_unattributed_usage)"
  type        = bool
  default     = false
}
