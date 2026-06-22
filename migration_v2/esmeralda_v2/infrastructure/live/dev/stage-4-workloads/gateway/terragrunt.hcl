# infrastructure/live/dev/stage-4-workloads/gateway/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  gateway_product = local.env_vars.locals.gateway_product
}

terraform {
  source = "../../../../../modules//4-workloads/gateways/${local.gateway_product}"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
}

inputs = {
  project_id = dependency.projects.outputs.gateway_project_id
  region     = local.env_vars.locals.region
  vpc_id     = dependency.networking.outputs.network_id
  subnet_id  = dependency.networking.outputs.subnet_id
}
