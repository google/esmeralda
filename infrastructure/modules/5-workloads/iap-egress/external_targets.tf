# infrastructure/modules/5-workloads/iap-egress/external_targets.tf
# ====================================================================
# Agent Registry entries of the catalog's managed: false targets (catalog/.resolved/<env>.json
# `external_targets`): legacy APIs, agents on other clouds, external gateways such as an Apigee
# model gateway. The Agent Gateway denies any host that isn't registered, and IAP egress needs the
# exact hostname, so each target gets its own entry (service_id = target name):
#   registry = endpoint    -> endpoint entry (http, non-Google model), NO_SPEC
#   registry = mcp_server  -> MCP server entry, NO_SPEC (the tools live on the server)
#   registry = agent       -> agent entry with an A2A card generated from the catalog
# Who may call each one is its target_iap_members list (main.tf), compiled from the target's
# catalog callers; removing the target deletes the entry, so the gateway denies the host again.
#
# The entries are created only while the registry root holds no roles/iap.egressor binding: a root
# binding (written by layer 4 before the per-entry system endpoint grants) would be inherited by
# every new entry and open it to every agent project. `require_closed_registry_root` checks it.
# ====================================================================

locals {
  external_urls = {
    for name, t in var.external_targets :
    name => replace(replace(t.url, "{domain}", var.domain), "{region}", var.region)
  }
  unresolved_external_urls = [for name, url in local.external_urls : name if can(regex("[{}]", url))]
}

resource "null_resource" "require_closed_registry_root" {
  count = length(var.external_targets) > 0 ? 1 : 0

  triggers = {
    external_urls = md5(jsonencode(local.external_urls))
    version       = "1"
  }

  lifecycle {
    precondition {
      condition     = length(local.unresolved_external_urls) == 0
      error_message = "external_targets URLs with a placeholder left (set var.domain from layer 2 entrypoint_domain): ${join(", ", local.unresolved_external_urls)}"
    }
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      IAP_MODE              = "check-root"
      GOVERNANCE_PROJECT_ID = var.governance_project_id
      REGION                = var.region
    }
  }
}

resource "google_agent_registry_service" "external" {
  for_each     = var.external_targets
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = each.key
  display_name = each.key
  description  = each.value.description

  dynamic "interfaces" {
    for_each = each.value.registry == "agent" ? [] : [local.external_urls[each.key]]
    content {
      url              = interfaces.value
      protocol_binding = each.value.protocol_binding
    }
  }

  dynamic "endpoint_spec" {
    for_each = each.value.registry == "endpoint" ? [1] : []
    content {
      type = "NO_SPEC"
    }
  }

  dynamic "mcp_server_spec" {
    for_each = each.value.registry == "mcp_server" ? [1] : []
    content {
      type = "NO_SPEC"
    }
  }

  dynamic "agent_spec" {
    for_each = each.value.registry == "agent" ? [1] : []
    content {
      type = "A2A_AGENT_CARD"
      content = jsonencode({
        name               = each.key
        description        = each.value.description
        version            = "1.0.0"
        capabilities       = {}
        defaultInputModes  = ["text/plain"]
        defaultOutputModes = ["application/json"]
        supportedInterfaces = [{
          url             = local.external_urls[each.key]
          protocolBinding = each.value.protocol_binding
          protocolVersion = "0.3"
        }]
        skills = []
      })
    }
  }

  depends_on = [null_resource.require_closed_registry_root]
}
