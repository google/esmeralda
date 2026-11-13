# infrastructure/live/dev/layer-5-workloads/services/corporate-email/terragrunt.hcl
# Layer 5: MCP server on Cloud Run + its entry in the central governance Agent Registry.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../../modules//5-workloads/services/corporate-email"
}

dependency "cicd" {
  config_path = "../../../../shared/layer-0-cicd"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
}

dependency "security" {
  config_path = "../../../layer-3-security"
}

# Catalog agents: an agent caller calls with its own Agent Identity, so it gets run.invoker (and its
# caller guard entry) as its principal:// (agent_identity_principal). Agents never depend on MCP
# services, so this adds no cycle; a new catalog agent needs its block here and its identity_refs entry.
dependency "cx_mortgage_orchestrator" {
  config_path = "../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-cx-mortgage-orchestrator"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-ai-coe-mortgage-specialist"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

# Apigee partner front door (partner_access = "apigee"): its proxy runtime service account is the
# identity of the catalog caller apigee-partner-mcp.
dependency "apigee" {
  config_path = "../../apigee/platform"
  enabled     = local.apigee_partner
  mock_outputs = {
    proxy_runtime_sa_email = "sa-apigee-proxy-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  gateways = local.env_vars.locals.gateways
  # This service's catalog entry (env.yaml catalog), keyed by the unit's directory name.
  name    = basename(get_terragrunt_dir())
  callers = try(local.env_vars.locals.catalog[local.name].callers, [])
  # Callers that are catalog agents (they call with their own Agent Identity), and the rest.
  agent_callers = [
    for caller in local.callers : caller
    if contains(["a2a", "adk"], try(local.env_vars.locals.catalog[caller].kind, ""))
  ]
  # Deprecated fallback (env.yaml legacy_shared_invoker): agents impersonate the shared invoker again.
  legacy_shared_invoker = try(local.env_vars.locals.legacy_shared_invoker, false)
  apigee_partner = (
    try(local.env_vars.locals.governance.apigee.enabled, false) &&
    try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee"
  )

  # Image: pinned by digest when the env lists it in image_digests (prd: release/prd.yaml,
  # written by `make promote`), otherwise <image>:<container_tag> (resolved to a digest by the module).
  image_name   = "corporate-email"
  image_digest = lookup(lookup(local.env_vars.locals, "image_digests", {}), local.image_name, "")
  image_ref    = local.image_digest != "" ? "${local.image_name}@${local.image_digest}" : "${local.image_name}:${local.env_vars.locals.container_tag}"
}

inputs = {
  project_id            = dependency.projects.outputs.mcps_project_id
  governance_project_id = dependency.projects.outputs.governance_project_id
  environment           = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  network_id            = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  container_image       = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/${local.image_ref}"
  tools_spec_path       = "${get_repo_root()}/apps/services/corporate-email/tools.json"
  internal_hostname     = "${local.name}.${dependency.networking.outputs.entrypoint_domain}"

  # roles/run.invoker, from the catalog callers: each agent caller's Agent Identity principal (a
  # `<unit>:agent_identity_principal` reference, resolved through identity_refs below); the service
  # accounts of the other callers (apigee-partner-mcp: the Apigee proxy runtime service account, only
  # with partner_access = "apigee"); and the identity of each enabled adapter, which calls with its own
  # token. direct adds none: callers reach the service with their own ID token. With the deprecated
  # legacy_shared_invoker fallback, agents call as the shared MCP invoker service account instead.
  invoker_service_accounts = distinct(compact(concat(
    [for caller in local.agent_callers : "${local.env_vars.locals.catalog[caller].unit}:agent_identity_principal"],
    [for caller in local.callers : (
      caller == "apigee-partner-mcp"
      ? (local.apigee_partner ? dependency.apigee.outputs.proxy_runtime_sa_email : "")
      : dependency.security.outputs["${replace(caller, "-", "_")}_sa_email"]
    ) if !contains(local.agent_callers, caller)],
    local.legacy_shared_invoker && length(local.agent_callers) > 0 ? [dependency.security.outputs.mcp_invoker_sa_email] : [],
    contains(local.gateways.enabled, "envoy") ? [try(dependency.security.outputs.envoy_sa_email, "")] : [],
  )))

  # Catalog caller guard (ESMERALDA_CALLER_ALLOWLIST, read by caller_guard.py in the image): this
  # service's allowlist compiled from the catalog (attribution.allowlists), its refs mapped to unit
  # outputs below. Off when env.yaml attribution.caller_guard is false.
  caller_allowlist = try(local.env_vars.locals.attribution.caller_guard, false) ? {
    policy  = local.env_vars.locals.catalog_attribution.policy
    callers = try(local.env_vars.locals.catalog_attribution.allowlists[local.name], [])
  } : null
  identity_refs = merge(
    { for k, v in dependency.security.outputs : "layer-3-security:${k}" => v if can(regex("_sa_email$", k)) && v != null },
    # agents/<name>:agent_identity_principal: each agent's Agent Identity principal (principal://).
    {
      "agents/cx-mortgage-orchestrator:agent_identity_principal"   = dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal
      "agents/ai-coe-mortgage-specialist:agent_identity_principal" = dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal
    },
    {
      for k, v in { "layer-5-workloads/apigee/platform:proxy_runtime_sa_email" = try(dependency.apigee.outputs.proxy_runtime_sa_email, null) } :
      k => v if local.apigee_partner && v != null
    },
  )
  # An enabled adapter calls with its own token: accepted, but it carries no team (the gateway
  # record attributes those calls).
  # With the deprecated legacy_shared_invoker fallback, agents call as the shared invoker service
  # account: accepted too (no team, several agents present it), so that mode is purely additive.
  extra_allowed_callers = concat(
    contains(local.gateways.enabled, "envoy") ? [
      { principal = try(dependency.security.outputs.envoy_sa_email, ""), caller = "gateway-adapter-envoy" },
    ] : [],
    local.legacy_shared_invoker && length(local.agent_callers) > 0 ? [
      { principal = dependency.security.outputs.mcp_invoker_sa_email, caller = "legacy-shared-invoker", shared = true },
    ] : [],
  )
}
