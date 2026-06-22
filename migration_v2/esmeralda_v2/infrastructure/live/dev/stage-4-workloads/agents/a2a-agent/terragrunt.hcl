# infrastructure/live/dev/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
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
  project_id            = dependency.projects.outputs.a2a_project_id
  region                = local.env_vars.locals.region
  vpc_id                = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email

  # Packaging paths for the ADK bundle (dynamically evaluated relative to root)
  pickle_object_path    = "${get_parent_terragrunt_dir()}/../app/agents/a2a-agent/dist/agent.pkl"
  requirements_path     = "${get_parent_terragrunt_dir()}/../app/agents/a2a-agent/dist/requirements.txt"
  dependencies_path     = "${get_parent_terragrunt_dir()}/../app/agents/a2a-agent/dist/dependencies.tar.gz"

  # Optional PSC attachment
  network_attachment    = dependency.networking.outputs.psc_network_attachment_id

  database_name         = "a2a_tasks"
}
