# infrastructure/modules/5-workloads/iap-egress/adopted_targets.tf
# ====================================================================
# Governance Agent Registry entries of adopted Agent Runtime agents (catalog/.resolved/<env>.json
# `adopted_targets`, present only when a catalog file adopts a registry entry with a
# deploy.registry pin and lists its callers).
#
# Agent Runtime registers each engine in the Agent Registry of the engine's own project, but the
# Agent Gateway reads only the governance registry (layer 4 `registries`), so a policy on that
# auto-registered entry would never be enforced. Each adopted engine therefore gets an
# Esmeralda-owned agent entry here (service_id = target name), on URLs derived only from its
# runtime resource (the Vertex AI REST paths of that engine). Registry lookup is longest-prefix, so
# these paths win over the host-level {region}-aiplatform Google API entry for that engine, and the
# entry's target_iap_members list (main.tf, iap_egress.agents[<name>]) decides who may call it.
# Removing the adoption deletes the entry and its policy; the engine falls back to the host-level
# entry, where reaching it still needs Agent Runtime IAM.
#
# Placeholders: {region}, {project_number:<alias>} (the engine's layer-1 project), and, for the
# derived principal of an adopted agent that is itself a caller, the project's Agent Identity trust
# domain (the shared trust-domain module, so org_id / folder handling matches the agent units).
# ====================================================================

locals {
  adopted_projects = {
    for alias in distinct([for t in values(var.adopted_targets) : t.project]) :
    alias => lookup(var.project_ids, alias, "")
  }
  adopted_missing_projects = sort([for alias, id in local.adopted_projects : alias if id == ""])

  adopted_number = {
    for name, t in var.adopted_targets : name => try(data.google_project.adopted[t.project].number, "")
  }
  adopted_location = {
    for name, t in var.adopted_targets : name => replace(t.location, "{region}", var.region)
  }
  adopted_urls = {
    for name, t in var.adopted_targets : name => [
      for u in t.urls : replace(replace(u, "{region}", var.region), "{project_number:${t.project}}", local.adopted_number[name])
    ]
  }
  adopted_registry_ids = {
    for name, t in var.adopted_targets :
    name => replace(replace(t.registry_id, "{region}", var.region), "{project_number:${t.project}}", local.adopted_number[name])
  }
  adopted_descriptions = {
    for name, t in var.adopted_targets :
    name => replace(replace(t.description, "{region}", var.region), "{project_number:${t.project}}", local.adopted_number[name])
  }
  unresolved_adopted = sort([
    for name, urls in local.adopted_urls : name
    if local.adopted_number[name] == "" || anytrue([for u in urls : can(regex("[{}]", u))]) || can(regex("[{}]", local.adopted_location[name]))
  ])

  # Derived principal of each adopted agent (its Agent Identity), for the
  # `adopted/<name>:agent_identity_principal` references of the compiled lists and attribution rows.
  adopted_principals = {
    for name, t in var.adopted_targets :
    "adopted/${name}:agent_identity_principal" => "principal://${module.adopted_trust_domain[t.project].trust_domain}/resources/aiplatform/projects/${local.adopted_number[name]}/locations/${local.adopted_location[name]}/reasoningEngines/${t.engine}"
    if contains(keys(module.adopted_trust_domain), t.project)
  }
}

data "google_project" "adopted" {
  for_each   = { for alias, id in local.adopted_projects : alias => id if id != "" }
  project_id = each.value
}

module "adopted_trust_domain" {
  source     = "../../_shared/agent_identity_trust_domain"
  for_each   = { for alias, id in local.adopted_projects : alias => id if id != "" }
  project_id = each.value
  org_id     = var.org_id
}

resource "google_agent_registry_service" "adopted" {
  for_each     = var.adopted_targets
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = each.key
  display_name = each.key
  description  = local.adopted_descriptions[each.key]

  agent_spec {
    type = "A2A_AGENT_CARD"
    content = jsonencode({
      name               = each.key
      description        = local.adopted_descriptions[each.key]
      version            = "1.0.0"
      capabilities       = {}
      defaultInputModes  = ["text/plain"]
      defaultOutputModes = ["application/json"]
      supportedInterfaces = [for u in local.adopted_urls[each.key] : {
        url             = u
        protocolBinding = each.value.protocol_binding
        protocolVersion = "0.3"
      }]
      # The registry requires at least one skill on an A2A card; the engine's real skills stay in
      # its own (auto-registered) entry.
      skills = [{
        id          = "query"
        name        = "query"
        description = "Calls of the adopted Agent Runtime engine (:query, :streamQuery and its sub-resources)."
        tags        = ["adopted"]
      }]
    })
  }

  lifecycle {
    precondition {
      condition     = length(local.adopted_missing_projects) == 0
      error_message = "adopted_targets in projects without a layer-1 <alias>_project_id output (project_ids): ${join(", ", local.adopted_missing_projects)}."
    }
    precondition {
      condition     = length(local.unresolved_adopted) == 0
      error_message = "adopted_targets with a placeholder left in their URLs: ${join(", ", local.unresolved_adopted)}."
    }
  }

  depends_on = [null_resource.require_closed_registry_root]
}
