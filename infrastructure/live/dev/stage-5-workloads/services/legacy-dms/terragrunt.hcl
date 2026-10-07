# infrastructure/live/dev/stage-5-workloads/services/legacy-dms/terragrunt.hcl
# Layer 5: MCP server on Cloud Run + its entry in the central governance Agent Registry.
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//5-workloads/services/legacy-dms"
}

dependency "cicd" {
  config_path = "../../../../shared/stage-0-cicd"
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
  project_id            = dependency.projects.outputs.mcps_project_id
  governance_project_id = dependency.projects.outputs.governance_project_id
  environment           = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  network_id            = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  container_image       = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/legacy-dms:${local.env_vars.locals.container_tag}"
  tools_spec_path       = "${get_repo_root()}/apps/services/legacy-dms/tools.json"
  invoker_service_accounts = [
    dependency.security.outputs.cx_mortgage_orchestrator_sa_email,
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.kong_sa_email
  ]
}
