# infrastructure/live/dev/stage-2-networking/terragrunt.hcl
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
  governance_project_id = dependency.projects.outputs.governance_project_id
  service_project_ids   = [
    dependency.projects.outputs.gateway_project_id,
    dependency.projects.outputs.mcps_project_id,
    dependency.projects.outputs.a2a_project_id,
    dependency.projects.outputs.root_project_id
  ]
  region                = local.env_vars.locals.region
  byo_networking        = local.env_vars.locals.byo_networking
}
