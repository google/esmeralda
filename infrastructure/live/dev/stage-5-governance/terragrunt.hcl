# Terragrunt Stage 5 Governance Stack Configuration
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//5-governance"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

# Dependency on Stage 1 Projects output
dependency "projects" {
  config_path = "../stage-1-projects"
}

inputs = {
  environment                  = local.env_vars.locals.environment
  governance_project_id        = dependency.projects.outputs.governance_project_id
  spoke_project_ids            = [
    dependency.projects.outputs.net_host_project_id,
    dependency.projects.outputs.gateway_project_id,
    dependency.projects.outputs.cicd_project_id,
    dependency.projects.outputs.mcps_project_id,
    dependency.projects.outputs.a2a_project_id,
    dependency.projects.outputs.root_project_id
  ]
  alert_email_address          = "esmeralda.secops@google.com"
  runaway_loop_token_threshold = 50000
}


