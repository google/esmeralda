# infrastructure/live/dev/layer-5-workloads/services/kong/terragrunt.hcl
# Layer 5 (last workload): Kong API gateway behind the internal HTTPS LB for *.esmeralda.internal.
# Applied after the MCP services and agents, so routes get the real engine IDs on the first apply.
# Mocks below only serve `validate`/`plan` of a not-yet-deployed env; `apply` requires real outputs.
include "root" {
  path = find_in_parent_folders()
}

locals {
  env_vars        = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  gateway_product = local.env_vars.locals.gateway_product
}

terraform {
  source = "../../../../../modules//5-workloads/services/${local.gateway_product}"
}

dependency "cicd" {
  config_path = "../../../../shared/layer-0-cicd"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
}

dependency "security" {
  config_path = "../../../layer-3-security"
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    engine_id    = "mock-engine-id"
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    engine_id    = "mock-engine-id"
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "corporate_email" {
  config_path = "../corporate-email"
  mock_outputs = {
    service_name = "mock-service"
    service_uri  = "https://mock-service.a.run.app"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "income_verification" {
  config_path = "../income-verification"
  mock_outputs = {
    service_name = "mock-service"
    service_uri  = "https://mock-service.a.run.app"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "legacy_dms" {
  config_path = "../legacy-dms"
  mock_outputs = {
    service_name = "mock-service"
    service_uri  = "https://mock-service.a.run.app"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  project_id          = dependency.projects.outputs.gateway_project_id
  environment         = local.env_vars.locals.environment
  region              = local.env_vars.locals.region
  vpc_id              = dependency.networking.outputs.network_id
  subnet_id           = dependency.networking.outputs.subnet_id
  net_host_project_id = dependency.projects.outputs.net_host_project_id
  dns_zone_name       = dependency.networking.outputs.dns_zone_name
  kong_image          = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/kong-gateway:${local.env_vars.locals.container_tag}"

  # *.esmeralda.internal leaf is signed by the layer-3 internal Root CA
  internal_ca_cert_pem = dependency.security.outputs.internal_ca_cert_pem
  internal_ca_key_pem  = dependency.security.outputs.internal_ca_key_pem

  invoker_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.ai_coe_mortgage_specialist_sa_email,
    dependency.security.outputs.cx_mortgage_orchestrator_sa_email,
    dependency.security.outputs.mcp_invoker_sa_email
  ]

  agent_endpoints = {
    # Agents
    ai-coe-mortgage-specialist = {
      logical_name = "ai-coe-mortgage-specialist"
      engine_id    = dependency.ai_coe_mortgage_specialist.outputs.engine_id
      endpoint_url = "${dependency.ai_coe_mortgage_specialist.outputs.endpoint_url}/a2a"
      audience     = "https://${local.env_vars.locals.region}-aiplatform.googleapis.com"
    }
    cx-mortgage-orchestrator = {
      logical_name = "cx-mortgage-orchestrator"
      engine_id    = dependency.cx_mortgage_orchestrator.outputs.engine_id
      endpoint_url = "${dependency.cx_mortgage_orchestrator.outputs.endpoint_url}:streamQuery?alt=sse"
      audience     = "https://${local.env_vars.locals.region}-aiplatform.googleapis.com"
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
