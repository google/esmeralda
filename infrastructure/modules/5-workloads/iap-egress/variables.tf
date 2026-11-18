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
  description = "Per-target IAM members granted roles/iap.egressor on specific Agent Registry agents, external endpoints, MCP servers and per-agent Google API entries (under endpoints, keyed by service id, {region} substituted). Members may be `<unit>:<output>` references (the catalog-compiled lists in catalog/.resolved/<env>.json `iap_egress`), resolved through identity_refs. An empty list writes an empty policy (nobody)"
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

variable "adopted_targets" {
  description = "Governance Agent Registry entries of adopted Agent Runtime agents (catalog/.resolved/<env>.json `adopted_targets`), keyed by target name (the entry's service_id): the engine's layer-1 project alias, location and engine id, the URLs derived from its runtime resource ({region} and {project_number:<alias>} substituted here), the pinned registry id and the description. Their caller lists are target_iap_members.agents[<name>]"
  type = map(object({
    registry         = string
    registry_id      = string
    source           = string
    runtime          = string
    runtime_resource = string
    project          = string
    location         = string
    engine           = string
    urls             = list(string)
    protocol_binding = string
    description      = string
    owner_team       = optional(string, "")
    principal        = optional(string, "")
  }))
  default = {}

  validation {
    condition     = alltrue([for t in values(var.adopted_targets) : t.registry == "agent" && t.runtime == "agent_runtime"])
    error_message = "adopted_targets[*] must be agent entries of agent_runtime engines (the only runtime adoption supports)."
  }
  validation {
    condition     = alltrue([for t in values(var.adopted_targets) : length(t.urls) > 0 && alltrue([for u in t.urls : startswith(u, "https://")])])
    error_message = "adopted_targets[*].urls must be a non-empty list of https URLs."
  }
}

variable "project_ids" {
  description = "Layer-1 project ids by alias (<alias>_project_id outputs without the suffix): the projects of adopted engines ({project_number:<alias>} and their Agent Identity trust domain)"
  type        = map(string)
  default     = {}
}

variable "org_id" {
  description = "Optional organization ID (ESMERALDA_ORG_ID) for the Agent Identity trust domain of adopted engines' projects under a folder (same handling as the agent units)"
  type        = string
  default     = ""
}

variable "identity_refs" {
  description = "Value of each `<unit>:<output>` reference target_iap_members may use (e.g. layer-3-security:test_vm_sa_email => the test VM SA email, agents/<name>:agent_identity_principal => its principal://). A reference without a value fails the plan; members that are not references pass through unchanged"
  type        = map(string)
  default     = {}
}

variable "agent_caller_grants" {
  description = "roles/aiplatform.user grants for agents that call other agents (catalog callers of a2a/adk targets): the callee agent's project and the caller's Agent Identity principal, usually as an `agents/<name>:agent_identity_principal` reference resolved through identity_refs"
  type = list(object({
    project_id = string
    member     = string
  }))
  default = []
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
