# infrastructure/modules/_shared/agent_identity_trust_domain/resolve/main.tf
# ==============================================================================
# AGENT IDENTITY TRUST DOMAIN: PURE RESOLUTION LOGIC (no provider, no API call)
# ==============================================================================
# The single place that builds the Agent Identity trust domain. The parent module looks up the
# project and, for a project under a folder, the folder's organization; this module only turns them
# into the trust domain, so the logic can be tested offline
# (tests/security/test_agent_identity_trust_domain.py).
#
#   project in an organization (direct child or under folders) -> agents.global.org-<ORG_ID>.system.id.goog
#   project with no organization                               -> agents.global.proj-<NUMBER>.system.id.goog
#
# The google_project data source returns an empty org_id (and a folder_id) for a project whose parent
# is a folder, so for such a project the organization comes from the folder. A project under a
# folder always belongs to an organization: if none can be resolved, fail instead of falling back to
# the project-scoped pool (which does not exist for such a project).

terraform {
  required_version = ">= 1.5.0"
}

variable "project_number" {
  description = "Numeric project number of the agent project."
  type        = string
}

variable "project_org_id" {
  description = "org_id of the project (google_project.org_id): set only when the organization is the direct parent."
  type        = string
  default     = ""
}

variable "project_folder_id" {
  description = "folder_id of the project (google_project.folder_id): set when the direct parent is a folder."
  type        = string
  default     = ""
}

variable "folder_organization" {
  description = "Organization of the parent folder (google_folder.organization with lookup_organization, organizations/<n>)."
  type        = string
  default     = ""
}

variable "org_id" {
  description = "Optional organization ID (numeric, or organizations/<n>), used only for a project under a folder whose organization the folder lookup did not return."
  type        = string
  default     = ""
}

locals {
  project_org_id      = trimprefix(trimspace(var.project_org_id), "organizations/")
  in_folder           = trimspace(var.project_folder_id) != ""
  folder_organization = trimprefix(trimspace(var.folder_organization), "organizations/")
  fallback_org_id     = trimprefix(trimspace(var.org_id), "organizations/")

  resolved_org_id = (
    local.project_org_id != "" ? local.project_org_id :
    !local.in_folder ? "" :
    local.folder_organization != "" ? local.folder_organization :
    local.fallback_org_id
  )

  scope        = local.resolved_org_id != "" ? "org-${local.resolved_org_id}" : "proj-${var.project_number}"
  trust_domain = "agents.global.${local.scope}.system.id.goog"
}

output "org_id" {
  description = "Organization ID the trust domain is scoped to (empty for a project with no organization)."
  value       = local.resolved_org_id
}

output "trust_domain" {
  description = "Agent Identity trust domain of the project."
  value       = local.trust_domain

  precondition {
    condition     = !local.in_folder || local.resolved_org_id != ""
    error_message = "Agent Identity trust domain: project number ${var.project_number} is in folder ${var.project_folder_id}, but the organization of that folder could not be resolved. Grant the deployer resourcemanager.folders.get on the folder (or pass org_id to the module). Refusing to fall back to the project-scoped pool, which does not exist for a project in an organization."
  }

  precondition {
    condition     = local.resolved_org_id == "" || can(regex("^[0-9]+$", local.resolved_org_id))
    error_message = "Agent Identity trust domain: organization ID \"${local.resolved_org_id}\" must be numeric."
  }

  precondition {
    condition     = can(regex("^[0-9]+$", var.project_number))
    error_message = "Agent Identity trust domain: project number \"${var.project_number}\" must be numeric."
  }
}
