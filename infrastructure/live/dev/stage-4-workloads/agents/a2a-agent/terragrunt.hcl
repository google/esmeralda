# infrastructure/live/dev/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
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

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_id            = dependency.projects.outputs.a2a_project_id
  region                = local.env_vars.locals.region
  vpc_id                = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  vpc_name              = element(split("/", dependency.networking.outputs.network_id), 4)
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email

  invoker_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.root_agent_sa_email,
    "kong-gateway-sa-${local.env_vars.locals.environment}@esmeralda-gateway-918f.iam.gserviceaccount.com"
  ]

  # BYOC Container Image URI
  agent_image_uri       = "${local.env_vars.locals.region}-docker.pkg.dev/${dependency.projects.outputs.cicd_project_id}/esmeralda-containers/a2a-agent:latest"




  psc_subnet_id         = dependency.networking.outputs.psc_subnet_id
  enable_psc_network    = true

  database_name         = "a2a_tasks"

  # Path to application YAML configuration
  agent_config_path     = "${get_repo_root()}/app/agents/a2a-agent/agent.yaml"
  agent_card_json       = file("${get_repo_root()}/app/agents/a2a-agent/agent-card.json")
}


