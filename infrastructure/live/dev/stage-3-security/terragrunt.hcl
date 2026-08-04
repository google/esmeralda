# infrastructure/live/dev/stage-3-security/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//3-security"
}

dependency "projects" {
  config_path = "../stage-1-projects"
}

locals {
  env_vars       = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  byo_networking = lookup(local.env_vars.locals, "byo_networking", false)
}

dependency "networking" {
  config_path = "../stage-2-networking"
  
  # Avoid running output lookups on skipped modules
  skip_outputs = local.byo_networking
  
  # Satisfy parser during evaluation with mock variables
  mock_outputs = {
    subnet_id = local.env_vars.locals.existing_subnet_id
  }
}

inputs = {
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  gateway_project_id    = dependency.projects.outputs.gateway_project_id
  cicd_project_id       = dependency.projects.outputs.cicd_project_id
  mcps_project_id       = dependency.projects.outputs.mcps_project_id

  a2a_project_id        = dependency.projects.outputs.a2a_project_id
  root_project_id       = dependency.projects.outputs.root_project_id
  governance_project_id = dependency.projects.outputs.governance_project_id
  project_suffix        = dependency.projects.outputs.project_suffix

  region                = local.env_vars.locals.region
  environment           = local.env_vars.locals.environment
  org_id                = lookup(local.env_vars.locals, "org_id", "")

  backend_subnet_id     = local.byo_networking ? local.env_vars.locals.existing_subnet_id : dependency.networking.outputs.subnet_id

  a2a_sql_service_agent            = dependency.projects.outputs.a2a_sql_service_agent
  governance_secrets_service_agent = dependency.projects.outputs.governance_secrets_service_agent
}

