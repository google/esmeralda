# infrastructure/live/prd/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
}

locals {
  env_vars       = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  byo_networking = lookup(local.env_vars.locals, "byo_networking", false)
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
  
  # Avoid running output lookups on skipped modules
  skip_outputs = local.byo_networking
  
  # Satisfy parser during evaluation with mock variables
  mock_outputs = {
    network_id = local.env_vars.locals.existing_vpc_id
    subnet_id  = local.env_vars.locals.existing_subnet_id
  }
}

inputs = {
  # Dynamically fetches workload project IDs provisioned dynamically by Esmeralda's Stage 1!
  project_id            = dependency.projects.outputs.a2a_project_id
  environment           = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email
  mcp_invoker_sa_email  = dependency.security.outputs.mcp_invoker_sa_email
  
  # Dynamic Fallback: Use client's existing VPC if BYO is active, else use dependency outputs
  vpc_id                = local.byo_networking ? local.env_vars.locals.existing_vpc_id  : dependency.networking.outputs.network_id
  subnet_id             = local.byo_networking ? local.env_vars.locals.existing_subnet_id : dependency.networking.outputs.subnet_id
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  vpc_name              = element(split("/", dependency.networking.outputs.network_id), 4)
  
  database_name         = "a2a_tasks"
  enable_iam_user       = true

  invoker_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.root_agent_sa_email,
    dependency.security.outputs.kong_sa_email
  ]

  agent_image_uri       = "${local.env_vars.locals.region}-docker.pkg.dev/${dependency.projects.outputs.cicd_project_id}/esmeralda-containers/a2a-agent:${lookup(local.env_vars.locals, "container_tag", "latest")}"
  psc_subnet_id         = dependency.networking.outputs.psc_subnet_id
  enable_psc_network    = true
  agent_config_path     = "${get_repo_root()}/apps/agents/a2a-agent/agent.yaml"
  agent_card_json       = jsonencode(yamldecode(file("${get_repo_root()}/apps/agents/a2a-agent/agent.yaml")).agent_card)
}
