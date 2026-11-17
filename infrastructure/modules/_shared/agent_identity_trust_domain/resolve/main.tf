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
# the project-scoped pool (which does not exist for such a project). An org_id input (ESMERALDA_ORG_ID)
# takes the place of the folder's organization, so no folder lookup is needed.

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
  description = "Optional organization ID (numeric, or organizations/<n>; ESMERALDA_ORG_ID). For a project under a folder it is used instead of the folder's organization (the parent module then skips the folder lookup)."
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
    local.fallback_org_id != "" ? local.fallback_org_id :
    local.folder_organization
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
    error_message = "Agent Identity trust domain: project number ${var.project_number} is in folder ${var.project_folder_id}, but the organization of that folder could not be resolved. Set ESMERALDA_ORG_ID (org_id in env.yaml) to the organization ID, or grant the identity running Terraform resourcemanager.folders.get on the folder and its parent folders (for example roles/browser). Refusing to fall back to the project-scoped pool, which does not exist for a project in an organization."
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
