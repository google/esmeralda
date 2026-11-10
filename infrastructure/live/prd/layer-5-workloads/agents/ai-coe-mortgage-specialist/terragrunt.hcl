# infrastructure/live/prd/layer-5-workloads/agents/ai-coe-mortgage-specialist/terragrunt.hcl
# Layer 5: AI CoE mortgage specialist (A2A) Reasoning Engine, bound to the layer-4 Agent Gateway.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../../modules//5-workloads/agents/a2a-agent"
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

dependency "governance" {
  config_path = "../../../layer-4-governance"
}

locals {
  env_cfg  = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  env_vars = merge(local.env_cfg.locals, { locals = local.env_cfg.locals })
  catalog  = local.env_vars.locals.catalog
  gateways = local.env_vars.locals.gateways
  # This agent's catalog entry (env.yaml catalog), keyed by the unit's directory name.
  name    = basename(get_terragrunt_dir())
  callers = try(local.catalog[local.name].callers, [])
  # Catalog hosts served by the built-in direct backend that take an access token (Agent Runtime):
  # this agent calls them with an access token instead of an ID token (ACCESS_TOKEN_HOSTS).
  access_token_names = [
    for name, entry in local.catalog : name
    if entry.token == "access" && lookup(local.gateways.host_rules, name, local.gateways.default) == "direct"
  ]

  # Image: pinned by digest when the env lists it in image_digests (prd: release/prd.yaml,
  # written by `make promote`), otherwise <image>:<container_tag> (resolved to a digest by the module).
  image_name   = "ai-coe-mortgage-specialist"
  image_digest = lookup(lookup(local.env_vars.locals, "image_digests", {}), local.image_name, "")
  image_ref    = local.image_digest != "" ? "${local.image_name}@${local.image_digest}" : "${local.image_name}:${local.env_vars.locals.container_tag}"
}

inputs = {
  project_id                = dependency.projects.outputs.ai_coe_agents_project_id
  governance_project_id     = dependency.projects.outputs.governance_project_id
  central_events_dataset_id = "esmeralda_telemetry_logs_${local.env_vars.environment}"
  environment               = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  vpc_id                = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  vpc_name              = element(split("/", dependency.networking.outputs.network_id), 4)
  agent_service_account = dependency.security.outputs.ai_coe_mortgage_specialist_sa_email
  mcp_invoker_sa_email  = dependency.security.outputs.mcp_invoker_sa_email

  # Agent Gateway binding + the CA bundle the env-neutral image installs at start-up
  agent_gateway_id                = dependency.governance.outputs.agent_gateway_id
  agent_gateway_root_certificates = dependency.governance.outputs.agw_root_ca_bundle

  # roles/aiplatform.user on this agent's project: the service accounts of the catalog callers; when an
  # agent calls, also the identity agents call out as (SERVICE_ACCOUNT_EMAIL, the shared MCP invoker
  # service account, whose access token reaches Agent Runtime through direct); and each enabled adapter.
  invoker_service_accounts = distinct(compact(concat(
    [for caller in local.callers : dependency.security.outputs["${replace(caller, "-", "_")}_sa_email"]],
    length([for caller in local.callers : caller if caller != "test-vm"]) > 0 ? [dependency.security.outputs.mcp_invoker_sa_email] : [],
    contains(local.gateways.enabled, "envoy") ? [try(dependency.security.outputs.envoy_sa_email, "")] : [],
  )))

  # Agent hosts reached through direct take an OAuth2 access token (esmeralda.auth.token_kind).
  access_token_hosts = [for name in local.access_token_names : "${name}.${dependency.networking.outputs.entrypoint_domain}"]

  # BYOC image from the shared layer-0 CI/CD project (resolved to a digest by the module)
  agent_image_uri = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/${local.image_ref}"

  psc_subnet_id      = dependency.networking.outputs.psc_subnet_id
  enable_psc_network = true

  agent_config_path = "${get_repo_root()}/apps/agents/ai-coe-mortgage-specialist/agent.yaml"
  agent_card_json   = jsonencode(yamldecode(file("${get_repo_root()}/apps/agents/ai-coe-mortgage-specialist/agent.yaml")).agent_card)
}
