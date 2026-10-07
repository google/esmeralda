# infrastructure/live/prd/stage-2-networking/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//2-networking"
}

dependency "projects" {
  config_path = "../stage-1-projects"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  gateway_project_id    = dependency.projects.outputs.gateway_project_id
  mcps_project_id       = dependency.projects.outputs.mcps_project_id
  ai_coe_agents_project_id        = dependency.projects.outputs.ai_coe_agents_project_id
  cx_agents_project_id       = dependency.projects.outputs.cx_agents_project_id
  governance_project_id = dependency.projects.outputs.governance_project_id
  project_suffix        = dependency.projects.outputs.project_suffix

  region                = local.env_vars.locals.region
  environment           = local.env_vars.locals.environment
  byo_networking        = local.env_vars.locals.byo_networking
  byo_net_host_project   = local.env_vars.locals.byo_net_host_project
  byo_gateway_project    = local.env_vars.locals.byo_gateway_project
  byo_governance_project = local.env_vars.locals.byo_governance_project

  existing_vpc_id       = local.env_vars.locals.existing_vpc_id
  existing_subnet_id    = local.env_vars.locals.existing_subnet_id

  mcps_run_service_agent    = dependency.projects.outputs.mcps_run_service_agent
  gateway_run_service_agent = dependency.projects.outputs.gateway_run_service_agent
  ai_coe_agents_run_service_agent     = dependency.projects.outputs.ai_coe_agents_run_service_agent
  ai_coe_agents_vertex_service_agent  = dependency.projects.outputs.ai_coe_agents_vertex_service_agent
  cx_agents_vertex_service_agent = dependency.projects.outputs.cx_agents_vertex_service_agent

  enable_secure_web_proxy   = false
}
