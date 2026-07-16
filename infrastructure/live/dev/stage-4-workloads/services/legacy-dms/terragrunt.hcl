# infrastructure/live/dev/stage-4-workloads/mcp-servers/legacy-dms/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/services/legacy-dms"
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
  project_id               = dependency.projects.outputs.mcps_project_id
  region                   = local.env_vars.locals.region
  network_id               = dependency.networking.outputs.network_id
  subnet_id                = dependency.networking.outputs.subnet_id
  container_image          = "${local.env_vars.locals.region}-docker.pkg.dev/${dependency.projects.outputs.cicd_project_id}/esmeralda-containers/legacy-dms:latest"
  invoker_service_accounts = [
    dependency.security.outputs.root_agent_sa_email,
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.kong_sa_email
  ]
}

