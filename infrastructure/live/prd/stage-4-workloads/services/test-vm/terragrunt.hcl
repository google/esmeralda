# infrastructure/live/dev/stage-4-workloads/services/test-vm/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/services/test-vm"
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
  project_id            = dependency.projects.outputs.root_project_id
  region                = local.env_vars.locals.region
  subnet_id             = dependency.networking.outputs.subnet_id
  service_account_email = dependency.security.outputs.test_vm_sa_email
  environment           = local.env_vars.locals.environment
}
