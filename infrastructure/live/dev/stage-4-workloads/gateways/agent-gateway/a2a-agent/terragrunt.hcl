# infrastructure/live/dev/stage-4-workloads/gateways/agent-gateway/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../../modules//4-workloads/gateways/agent-gateway"
}

dependency "projects" {
  config_path = "../../../../stage-1-projects"
}

dependency "networking" {
  config_path = "../../../../stage-2-networking"
}

dependency "security" {
  config_path = "../../../../stage-3-security"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_id                = dependency.projects.outputs.a2a_project_id
  region                    = local.env_vars.locals.region
  environment               = local.env_vars.locals.environment
  subnet_id                 = dependency.networking.outputs.agw_egress_subnet_id
  net_host_project_id       = dependency.projects.outputs.net_host_project_id
  network_id                = dependency.networking.outputs.network_id
  governance_project_id     = dependency.projects.outputs.governance_project_id
  model_armor_template_name = try(dependency.security.outputs.model_armor_template_name, "")
}
