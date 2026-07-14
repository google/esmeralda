# infrastructure/live/dev/stage-4-workloads/agents/base-adk-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/base-adk-agent"
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

dependency "a2a_agent" {
  config_path = "../a2a-agent"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_id            = dependency.projects.outputs.root_project_id
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.root_agent_sa_email

  # BYOC Container Image URI
  agent_image_uri       = "${local.env_vars.locals.region}-docker.pkg.dev/${dependency.projects.outputs.cicd_project_id}/esmeralda-containers/root-agent:latest"




  # Connect reasoning engine container inside private VPC via PSC Network Attachment
  network_attachment    = dependency.networking.outputs.psc_network_attachment_id

  # Inject downstream endpoints
  gateway_mcp_url       = ""
  a2a_agent_url         = dependency.a2a_agent.outputs.endpoint_url

  # Path to application YAML configuration
  agent_config_path     = "${get_repo_root()}/app/agents/base-adk-agent/agent.yaml"
}


