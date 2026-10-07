# infrastructure/modules/5-workloads/services/iap-egress/main.tf
# ====================================================================
# Final layer-5 step: grant roles/iap.egressor on every Agent Registry entry.
# --------------------------------------------------------------------
# Layer 4 governance already grants it registry-wide, but MCP servers and A2A agents only
# register themselves in layer 5. Re-running the same idempotent script once they exist
# keeps deploy-all single-pass (no governance re-apply after workloads).
# ====================================================================

terraform {
  required_version = ">= 1.5.0"
}

resource "null_resource" "grant_iap_egress" {
  triggers = {
    registry_entries = md5(jsonencode(sort(var.registry_service_ids)))
    members          = md5(jsonencode(sort(var.iap_egress_members)))
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      GOVERNANCE_PROJECT_ID = var.governance_project_id
      REGION                = var.region
      IAP_MEMBERS_JSON      = jsonencode(var.iap_egress_members)
    }
  }
}
