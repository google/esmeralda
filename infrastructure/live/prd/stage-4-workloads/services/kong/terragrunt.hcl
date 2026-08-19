# infrastructure/live/dev/stage-4-workloads/gateway/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  gateway_product = local.env_vars.locals.gateway_product
}

terraform {
  source = "../../../../../modules//4-workloads/services/${local.gateway_product}"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
}

dependency "a2a_agent" {
  config_path = "../../agents/a2a-agent"
  mock_outputs = {
    engine_id    = "mock-engine-id"
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1/projects/mock/locations/us-central1/reasoningEngines/12345"
  }
}

dependency "root_agent" {
  config_path = "../../agents/base-adk-agent"
  mock_outputs = {
    engine_id    = "mock-engine-id"
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1/projects/mock/locations/us-central1/reasoningEngines/12345"
  }
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "corporate_email" {
  config_path = "../corporate-email"
}

dependency "income_verification" {
  config_path = "../income-verification"
}

dependency "legacy_dms" {
  config_path = "../legacy-dms"
}

inputs = {
  project_id          = dependency.projects.outputs.gateway_project_id
  environment         = local.env_vars.locals.environment
  region              = local.env_vars.locals.region
  vpc_id              = dependency.networking.outputs.network_id
  subnet_id           = dependency.networking.outputs.subnet_id
  net_host_project_id = dependency.projects.outputs.net_host_project_id
  dns_zone_name       = dependency.networking.outputs.dns_zone_name
  kong_image          = "${local.env_vars.locals.region}-docker.pkg.dev/${dependency.projects.outputs.cicd_project_id}/esmeralda-containers/kong-gateway:${lookup(local.env_vars.locals, "container_tag", "latest")}"

  invoker_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.a2a_agent_sa_email,
    dependency.security.outputs.root_agent_sa_email,
    dependency.security.outputs.mcp_invoker_sa_email
  ]

  agent_endpoints = {
    # Agents
    a2a-agent = {
      logical_name = "a2a-mortgage-agent"
      engine_id    = dependency.a2a_agent.outputs.engine_id
      endpoint_url = "${dependency.a2a_agent.outputs.endpoint_url}/a2a"
      audience     = "https://us-central1-aiplatform.googleapis.com"
    }
    root-agent = {
      logical_name = "root-agent"
      engine_id    = dependency.root_agent.outputs.engine_id
      endpoint_url = "${dependency.root_agent.outputs.endpoint_url}:streamQuery?alt=sse"
      audience     = "https://us-central1-aiplatform.googleapis.com"
    }
    # MCP Servers
    corporate-email = {
      logical_name = "corporate-email"
      engine_id    = dependency.corporate_email.outputs.service_name
      endpoint_url = dependency.corporate_email.outputs.service_uri
      audience     = dependency.corporate_email.outputs.service_uri
    }
    income-verification = {
      logical_name = "income-verification"
      engine_id    = dependency.income_verification.outputs.service_name
      endpoint_url = dependency.income_verification.outputs.service_uri
      audience     = dependency.income_verification.outputs.service_uri
    }
    legacy-dms = {
      logical_name = "legacy-dms"
      engine_id    = dependency.legacy_dms.outputs.service_name
      endpoint_url = dependency.legacy_dms.outputs.service_uri
      audience     = dependency.legacy_dms.outputs.service_uri
    }
  }
}
