# infrastructure/live/prd/stage-5-workloads/agents/a2a-agent/terragrunt.hcl
# Layer 5: A2A Mortgage Specialist Reasoning Engine, bound to the layer-4 Agent Gateway.
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//5-workloads/agents/a2a-agent"
}

dependency "cicd" {
  config_path = "../../../../shared/stage-0-cicd"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "governance" {
  config_path = "../../../stage-4-governance"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_id            = dependency.projects.outputs.a2a_project_id
  governance_project_id = dependency.projects.outputs.governance_project_id
  environment           = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  vpc_id                = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  vpc_name              = element(split("/", dependency.networking.outputs.network_id), 4)
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email
  mcp_invoker_sa_email  = dependency.security.outputs.mcp_invoker_sa_email

  # Agent Gateway binding + the CA bundle the env-neutral image installs at start-up
  agent_gateway_id                = dependency.governance.outputs.agent_gateway_id
  agent_gateway_root_certificates = dependency.governance.outputs.agw_root_ca_bundle

  invoker_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.root_agent_sa_email,
    dependency.security.outputs.kong_sa_email
  ]

  # BYOC image from the shared layer-0 CI/CD project (resolved to a digest by the module)
  agent_image_uri = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/a2a-agent:${local.env_vars.locals.container_tag}"

  psc_subnet_id      = dependency.networking.outputs.psc_subnet_id
  enable_psc_network = true

  database_name = "a2a_tasks"

  agent_config_path = "${get_repo_root()}/apps/agents/a2a-agent/agent.yaml"
  agent_card_json   = jsonencode(yamldecode(file("${get_repo_root()}/apps/agents/a2a-agent/agent.yaml")).agent_card)
}
