# infrastructure/modules/5-workloads/iap-egress/main.tf
# ====================================================================
# Final layer-5 step: register the catalog's external targets and grant per-target
# roles/iap.egressor on Agent Registry entries.
# --------------------------------------------------------------------
# Layer 4 governance grants the agent projects roles/iap.egressor on each system Google API endpoint
# entry, and the registry root holds no binding (a root binding is inherited by every entry). This
# unit registers the catalog's managed: false targets (external_targets.tf) and, once MCP servers and
# A2A agents have registered themselves, writes the least-privilege policy of every agent, external
# endpoint and MCP server entry. The per-target lists come from the service catalog (target callers,
# compiled into catalog/.resolved/<env>.json `iap_egress`), the same callers that drive Cloud Run
# run.invoker and Agent Runtime IAM:
#   - cx-mortgage-orchestrator is listed only on ai-coe-mortgage-specialist (not on MCP tools)
#   - ai-coe-mortgage-specialist is listed only on the registered MCP servers (and, in dev, on the
#     external-probe endpoint)
# ====================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 8.4.0, < 9.0.0"
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

data "google_project" "governance" {
  project_id = var.governance_project_id
}

locals {
  iap_groups = ["agents", "endpoints", "mcp_servers"]

  # Catalog-compiled lists name identities by `<unit>:<output>` reference; the unit passes their
  # values in identity_refs. Anything that still looks like a reference afterwards is an error.
  ref_pattern = "^[a-z0-9_./-]+:[a-z0-9_]+$"
  resolved_target_iap_members = {
    for group in local.iap_groups : group => {
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
    for group, targets in local.resolved_target_iap_members : group => {
      for target_id, members in targets :
      target_id => distinct([
        for m in members :
        startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
        if m != "" && m != null
      ])
    }
  }

  # Every external target must have a list (possibly empty): an entry without one would keep
  # whatever policy it had.
  external_without_list = [
    for name, t in var.external_targets : name
    if !contains(keys(local.normalized_target_iap_members[lookup({ endpoint = "endpoints", mcp_server = "mcp_servers", agent = "agents" }, t.registry, "endpoints")]), name)
  ]

  # Agent callers of agent targets (catalog): roles/aiplatform.user for the caller's Agent Identity
  # principal on the callee agent's project. Granted here (after every agent) so agents never depend
  # on each other's units. Members are `<unit>:agent_identity_principal` references.
  resolved_agent_caller_grants = {
    for g in var.agent_caller_grants :
    "${g.project_id}|${lookup(var.identity_refs, g.member, g.member)}" => {
      project_id = g.project_id
      member     = lookup(var.identity_refs, g.member, g.member)
    }
  }
  unresolved_agent_caller_refs = distinct([
    for g in local.resolved_agent_caller_grants : g.member if can(regex(local.ref_pattern, g.member))
  ])
}

resource "google_project_iam_member" "agent_callers_aiplatform_user" {
  for_each = { for k, g in local.resolved_agent_caller_grants : k => g if !can(regex(local.ref_pattern, g.member)) }
  project  = each.value.project_id
  role     = "roles/aiplatform.user"
  member   = each.value.member

  lifecycle {
    precondition {
      condition     = length(local.unresolved_agent_caller_refs) == 0
      error_message = "agent_caller_grants references without a value in identity_refs: ${join(", ", local.unresolved_agent_caller_refs)}. Map them to unit outputs in the iap-egress terragrunt unit."
    }
  }
}

resource "null_resource" "grant_iap_egress" {
  depends_on = [google_agent_registry_service.external]

  triggers = {
    registry_entries          = md5(jsonencode(sort(var.registry_service_ids)))
    external_entries          = md5(jsonencode({ for k, v in google_agent_registry_service.external : k => v.id }))
    target_policies           = md5(jsonencode(local.normalized_target_iap_members))
    governance_project_number = data.google_project.governance.number
    version                   = "3"
  }

  lifecycle {
    precondition {
      condition     = length(local.unresolved_refs) == 0
      error_message = "target_iap_members references without a value in identity_refs: ${join(", ", local.unresolved_refs)}. Map them to unit outputs in the iap-egress terragrunt unit."
    }
    precondition {
      condition     = length(local.external_without_list) == 0
      error_message = "external targets without a target_iap_members list: ${join(", ", local.external_without_list)} (regenerate catalog/.resolved/<env>.json)."
    }
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      IAP_MODE                 = "targets"
      GOVERNANCE_PROJECT_ID    = var.governance_project_id
      REGION                   = var.region
      IAP_TARGET_POLICIES_JSON = jsonencode(local.normalized_target_iap_members)
    }
  }
}
