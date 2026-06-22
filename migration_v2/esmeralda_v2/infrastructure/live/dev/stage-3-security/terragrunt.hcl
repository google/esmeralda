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

dependency "networking" {
  config_path = "../stage-2-networking"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  governance_project_id = dependency.projects.outputs.governance_project_id
  project_ids = {
    net_host   = dependency.projects.outputs.net_host_project_id
    gateway    = dependency.projects.outputs.gateway_project_id
    mcps       = dependency.projects.outputs.mcps_project_id
    a2a        = dependency.projects.outputs.a2a_project_id
    root       = dependency.projects.outputs.root_project_id
    governance = dependency.projects.outputs.governance_project_id
  }
  region                = local.env_vars.locals.region
}
