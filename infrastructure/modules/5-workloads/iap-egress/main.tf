# infrastructure/modules/5-workloads/iap-egress/main.tf
# ====================================================================
# Final layer-5 step: grant per-target roles/iap.egressor on Agent Registry entries.
# --------------------------------------------------------------------
# Layer 4 governance grants roles/iap.egressor on the registry root, conditioned to system Google
# API endpoint entries only. This unit re-runs the same script with the same root inputs (so both
# writers produce the identical conditional root policy) and, once MCP servers and A2A agents have
# registered themselves, applies least-privilege per-target policies on their entries. The per-target
# lists come from the service catalog (target callers, compiled into catalog/.resolved/<env>.json
# `iap_egress`), the same callers that drive Cloud Run run.invoker and Agent Runtime IAM:
#   - cx-mortgage-orchestrator is listed only on ai-coe-mortgage-specialist (not on MCP tools)
#   - ai-coe-mortgage-specialist is listed only on the registered MCP servers
# ====================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }
  }
}

# IAP evaluates Agent Registry resource names by project number; the root condition uses it.
data "google_project" "governance" {
  project_id = var.governance_project_id
}

locals {
  normalized_registry_members = distinct([
    for m in var.iap_egress_members :
    startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
    if m != "" && m != null
  ])

  # Catalog-compiled lists name identities by `<unit>:<output>` reference; the unit passes their
  # values in identity_refs. Anything that still looks like a reference afterwards is an error.
  ref_pattern = "^[a-z0-9_./-]+:[a-z0-9_]+$"
  resolved_target_iap_members = {
    for group in ["mcp_servers", "agents"] : group => {
      for target_id, members in coalesce(var.target_iap_members[group], {}) :
      target_id => [for m in members : lookup(var.identity_refs, m, m) if m != "" && m != null]
    }
  }
  unresolved_refs = distinct(flatten([
    for group, targets in local.resolved_target_iap_members : [
      for target_id, members in targets : [for m in members : m if can(regex(local.ref_pattern, m))]
    ]
  ]))

  normalized_target_iap_members = {
    mcp_servers = {
      for target_id, members in local.resolved_target_iap_members.mcp_servers :
      target_id => distinct([
        for m in members :
        startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
        if m != "" && m != null
      ])
    }
    agents = {
      for target_id, members in local.resolved_target_iap_members.agents :
      target_id => distinct([
        for m in members :
        startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
        if m != "" && m != null
      ])
    }
  }

  has_target_policies = (
    length(local.normalized_target_iap_members.mcp_servers) > 0 ||
    length(local.normalized_target_iap_members.agents) > 0
  )
}

resource "null_resource" "grant_iap_egress" {
  triggers = {
    registry_entries          = md5(jsonencode(sort(var.registry_service_ids)))
    members                   = md5(jsonencode(sort(local.normalized_registry_members)))
    target_policies           = md5(jsonencode(local.normalized_target_iap_members))
    governance_project_number = data.google_project.governance.number
    version                   = "2"
  }

  lifecycle {
    precondition {
      condition     = length(local.unresolved_refs) == 0
      error_message = "target_iap_members references without a value in identity_refs: ${join(", ", local.unresolved_refs)}. Map them to unit outputs in the iap-egress terragrunt unit."
    }
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      GOVERNANCE_PROJECT_ID     = var.governance_project_id
      GOVERNANCE_PROJECT_NUMBER = data.google_project.governance.number
      REGION                    = var.region
      IAP_MEMBERS_JSON          = jsonencode(local.normalized_registry_members)
      IAP_TARGET_POLICIES_JSON  = local.has_target_policies ? jsonencode(local.normalized_target_iap_members) : ""
    }
  }
}
