include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/services/repository"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_id = dependency.projects.outputs.cicd_project_id
  region     = local.env_vars.locals.region
}

