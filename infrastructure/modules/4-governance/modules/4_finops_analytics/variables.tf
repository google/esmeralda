variable "environment" {
  type        = string
  description = "Target deployment environment (dev, stg, prod)"
}

variable "governance_project_id" {
  type        = string
  description = "GCP Project ID for central governance and telemetry"
}

variable "usage_dataset_id" {
  type        = string
  description = "BigQuery dataset (same project) holding the esmeralda.usage landing tables (modules/1_telemetry_sinks)"
}

variable "usage_landing_tables" {
  type        = list(string)
  description = "esmeralda.usage landing tables in usage_dataset_id that the usage_events view unions"

  validation {
    condition     = length(var.usage_landing_tables) > 0
    error_message = "usage_events needs at least one landing table."
  }
}

variable "project_numbers" {
  type        = map(string)
  default     = {}
  nullable    = false
  description = "Project number -> project ID of the projects that produce usage. Agent Runtime reports project numbers (e.g. in caller.declared.project_id); vw_usage_normalized maps them back to IDs."
}

variable "principal_type_values" {
  type = list(string)
  default = [
    "gcp_project",                  # a project ID: the project a record was logged in, or a declared caller project
    "agent_runtime",                # Agent Runtime agent identity (principal://... of the reasoning engine)
    "spiffe",                       # SPIFFE id from mTLS
    "service_account",              # Google service account email
    "group",                        # Google group
    "apigee_app",                   # Apigee developer app
    "workload_identity_federation", # federated principal (issuer, subject)
    "oidc",                         # OIDC subject
  ]
  nullable    = false
  description = "Accepted principal_type values in principal_teams (documented in the table schema, checked on var.principal_teams)."
}

variable "principal_teams" {
  type = list(object({
    principal      = string
    principal_type = string
    team           = string
    source         = optional(string, "terraform")
  }))
  default     = []
  nullable    = false
  description = "Principal -> team rows declared in Terraform, served with the principal_teams table through vw_principal_teams."

  validation {
    condition     = alltrue([for r in var.principal_teams : trimspace(r.principal) != "" && trimspace(r.team) != "" && can(regex("^[a-z0-9_]+$", r.principal_type))])
    error_message = "Each principal_teams row needs a principal, a team and a lowercase snake_case principal_type."
  }

  validation {
    condition     = length(var.principal_teams) == length(distinct([for r in var.principal_teams : "${r.principal_type}|${r.principal}"]))
    error_message = "principal_teams has duplicate (principal_type, principal) rows."
  }
}

variable "principal_teams_extra_columns" {
  type = list(object({
    name        = string
    type        = string
    mode        = optional(string, "NULLABLE")
    description = optional(string, "")
  }))
  default     = []
  nullable    = false
  description = "Extra principal_teams columns after (principal, principal_type, team, source). NULLABLE or REPEATED only, so BigQuery can add them in place."

  validation {
    condition     = alltrue([for c in var.principal_teams_extra_columns : contains(["NULLABLE", "REPEATED"], c.mode) && !contains(["principal", "principal_type", "team", "source"], c.name)])
    error_message = "principal_teams_extra_columns must be NULLABLE or REPEATED and must not redefine principal, principal_type, team or source."
  }
}
