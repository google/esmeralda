# infrastructure/live/dev/layer-5-workloads/iap-egress/terragrunt.hcl
# Layer 5 (final step): grant least-privilege per-target roles/iap.egressor on each Agent Registry
# entry once the MCP services and Reasoning Engine agents have registered themselves.
#
# Who may call each target comes from the service catalog: the callers in catalog/ compile into
# catalog/.resolved/<env>.json `iap_egress` (env.yaml catalog_iap_egress) as `<unit>:<output>`
# references; identity_refs below maps them to unit outputs. Adding a caller in catalog/ updates both
# the run.invoker / aiplatform.user grants and these gateway lists. A new agent caller also needs its
# dependency block and its identity_refs entry here (the plan fails on an unmapped reference).
#
# The catalog's managed: false targets (external_targets) are registered here too, as the last
# layer-5 step, so hosts known only in layer 5 never create a cycle; {domain} in their URLs is the
# layer-2 entry point domain. The registry root holds no binding (layer 4 grants the Google API
# system endpoints entry by entry), so each new entry is closed until its catalog callers are granted.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../modules//5-workloads/iap-egress"
}

dependency "projects" {
  config_path = "../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../layer-2-networking"
  mock_outputs = {
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "security" {
  config_path = "../../layer-3-security"
}

dependency "governance" {
  config_path = "../../layer-4-governance"
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-cx-mortgage-orchestrator"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    registry_service_id      = "mock-registry-service"
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-ai-coe-mortgage-specialist"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "corporate_email" {
  config_path = "../services/corporate-email"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "income_verification" {
  config_path = "../services/income-verification"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "legacy_dms" {
  config_path = "../services/legacy-dms"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

# Apigee partner front door (partner_access = "apigee"): its proxy runtime service account is the
# identity of the catalog caller apigee-partner-mcp (a principal_teams row when a target lists it).
dependency "apigee" {
  config_path = "../apigee/platform"
  enabled     = local.apigee_partner
  mock_outputs = {
    proxy_runtime_sa_email = "sa-apigee-proxy-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  apigee_partner = (
    try(local.env_vars.locals.governance.apigee.enabled, false) &&
    try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee"
  )
  # Verified attribution (env.yaml attribution.principal_teams): load the catalog principal_teams
  # rows and count gateway decisions for principals without one.
  attribution_enabled = try(local.env_vars.locals.attribution.principal_teams, false)
}

inputs = {
  governance_project_id = dependency.projects.outputs.governance_project_id
  region                = local.env_vars.locals.region
  registry_service_ids = [
    dependency.ai_coe_mortgage_specialist.outputs.registry_service_id,
    dependency.corporate_email.outputs.registry_service_id,
    dependency.income_verification.outputs.registry_service_id,
    dependency.legacy_dms.outputs.registry_service_id,
  ]

  # Per-target callers, compiled from the catalog ({agents = {...}, mcp_servers = {...}} of refs):
  # never hand-written here. The orchestrator is a caller of the specialist only, never of an MCP server.
  target_iap_members = local.env_vars.locals.catalog_iap_egress

  # Agent Registry entries of the catalog's managed: false targets (compiled, never hand-written).
  external_targets = local.env_vars.locals.catalog_external_targets
  domain           = dependency.networking.outputs.entrypoint_domain

  # Values of the references the compiled lists use.
  identity_refs = merge(
    # layer-3-security:<name>_sa_email: the service accounts layer 3 creates.
    { for k, v in dependency.security.outputs : "layer-3-security:${k}" => v if can(regex("_sa_email$", k)) && v != null },
    # agents/<name>:agent_identity_principal: each agent's Agent Identity principal (principal://).
    {
      "agents/cx-mortgage-orchestrator:agent_identity_principal"   = dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal
      "agents/ai-coe-mortgage-specialist:agent_identity_principal" = dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal
    },
    # layer-5-workloads/apigee/platform:proxy_runtime_sa_email: the Apigee proxy runtime identity.
    {
      for k, v in { "layer-5-workloads/apigee/platform:proxy_runtime_sa_email" = try(dependency.apigee.outputs.proxy_runtime_sa_email, null) } :
      k => v if local.apigee_partner && v != null
    },
  )

  # Verified attribution (attribution.tf): the catalog's principal -> team rows (refs resolved through
  # identity_refs) replace the `source = catalog` rows of the layer-4 principal_teams table, and the
  # governance project counts gateway decisions for principals without a row (expected: 0). Off until
  # layer 4 publishes the table (try: older layer-4 state has no such output).
  environment           = local.env_vars.locals.environment
  attribution_enabled   = local.attribution_enabled && try(dependency.governance.outputs.principal_teams_table_id, "") != ""
  principal_teams_rows  = try(local.env_vars.locals.catalog_attribution.principal_teams, [])
  principal_teams_table = "${dependency.projects.outputs.governance_project_id}.${try(dependency.governance.outputs.governance_dataset_id, "")}.${try(dependency.governance.outputs.principal_teams_table_id, "")}"
  notification_channels = try(dependency.governance.outputs.alert_notification_channels, [])
  alert_on_unattributed = try(dependency.governance.outputs.alert_on_unattributed_usage, false)
}
