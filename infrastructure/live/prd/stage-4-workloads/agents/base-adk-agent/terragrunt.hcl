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
  mcp_invoker_sa_email  = dependency.security.outputs.mcp_invoker_sa_email

  # BYOC Container Image URI
  agent_image_uri       = "${local.env_vars.locals.region}-docker.pkg.dev/${dependency.projects.outputs.cicd_project_id}/esmeralda-containers/root-agent:${lookup(local.env_vars.locals, "container_tag", "latest")}"




  vpc_id                = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  net_host_project_id   = dependency.projects.outputs.net_host_project_id
  vpc_name              = element(split("/", dependency.networking.outputs.network_id), 4)
  psc_subnet_id         = dependency.networking.outputs.psc_subnet_id
  enable_psc_network    = true

  # Inject downstream endpoints
  gateway_mcp_url       = ""
  a2a_agent_url         = "http://a2a-mortgage-agent.esmeralda.internal"

  # Path to application YAML configuration
  agent_config_path     = "${get_repo_root()}/apps/agents/base-adk-agent/agent.yaml"
}


