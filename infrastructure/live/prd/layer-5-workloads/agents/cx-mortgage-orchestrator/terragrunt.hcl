# infrastructure/live/prd/layer-5-workloads/agents/cx-mortgage-orchestrator/terragrunt.hcl
# Layer 5: CX mortgage orchestrator Reasoning Engine (CX team), bound to the layer-4 Agent Gateway.
# Calls the AI CoE mortgage specialist (A2A) at runtime through the gateway and Kong (ai-coe-mortgage-specialist.esmeralda.internal).
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//5-workloads/agents/adk-agent"
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

dependency "ai_coe_mortgage_specialist" {
  config_path = "../ai-coe-mortgage-specialist"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_id            = dependency.projects.outputs.cx_agents_project_id
  environment           = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.cx_mortgage_orchestrator_sa_email
  mcp_invoker_sa_email  = dependency.security.outputs.mcp_invoker_sa_email

  # Agent Gateway binding + the CA bundle the env-neutral image installs at start-up
  agent_gateway_id                = dependency.governance.outputs.agent_gateway_id
  agent_gateway_root_certificates = dependency.governance.outputs.agw_root_ca_bundle

  # BYOC image from the shared layer-0 CI/CD project (resolved to a digest by the module)
  agent_image_uri = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/cx-mortgage-orchestrator:${local.env_vars.locals.container_tag}"

  vpc_id              = dependency.networking.outputs.network_id
  subnet_id           = dependency.networking.outputs.subnet_id
  net_host_project_id = dependency.projects.outputs.net_host_project_id
  vpc_name            = element(split("/", dependency.networking.outputs.network_id), 4)
  psc_subnet_id       = dependency.networking.outputs.psc_subnet_id
  enable_psc_network  = true

  # Downstream endpoints
  gateway_mcp_url = ""
  a2a_agent_url   = "https://ai-coe-mortgage-specialist.esmeralda.internal"

  agent_config_path = "${get_repo_root()}/apps/agents/cx-mortgage-orchestrator/agent.yaml"
}
