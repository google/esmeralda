# infrastructure/modules/5-workloads/services/iap-egress/main.tf
# ====================================================================
# Final layer-5 step: grant per-target roles/iap.egressor on Agent Registry entries.
# --------------------------------------------------------------------
# Layer 4 governance grants roles/iap.egressor on the registry root for system Google API
# endpoints. Once MCP servers and A2A agents register themselves in layer 5, this stack
# applies least-privilege per-target IAP/UAP policies so:
#   - cx-mortgage-orchestrator can egress only to ai-coe-mortgage-specialist (not MCP tools)
#   - ai-coe-mortgage-specialist can egress only to the registered MCP servers
# ====================================================================

terraform {
  required_version = ">= 1.5.0"
}

locals {
  normalized_registry_members = distinct([
    for m in var.iap_egress_members :
    startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
    if m != "" && m != null
  ])

  normalized_target_iap_members = {
    mcp_servers = {
      for target_id, members in coalesce(var.target_iap_members.mcp_servers, {}) :
      target_id => distinct([
        for m in members :
        startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
        if m != "" && m != null
      ])
    }
    agents = {
      for target_id, members in coalesce(var.target_iap_members.agents, {}) :
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
    registry_entries = md5(jsonencode(sort(var.registry_service_ids)))
    members          = md5(jsonencode(sort(local.normalized_registry_members)))
    target_policies  = md5(jsonencode(local.normalized_target_iap_members))
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      GOVERNANCE_PROJECT_ID    = var.governance_project_id
      REGION                   = var.region
      IAP_MEMBERS_JSON         = jsonencode(local.normalized_registry_members)
      IAP_TARGET_POLICIES_JSON = local.has_target_policies ? jsonencode(local.normalized_target_iap_members) : ""
    }
  }
}
