# infrastructure/live/prd/layer-5-workloads/gateways/direct/terragrunt.hcl
# Layer 5 built-in backend "direct" (no proxy): per-host backends for every catalog entry (env.yaml
# `catalog`). MCP services get a serverless NEG + backend service in their own project; agents share
# one PSC NEG to the regional aiplatform API in the gateway project. The entrypoint routes hosts to
# them (gateways.default / host_rules); it owns no load balancer, certificate or DNS record.
# Always deployed, even when every host goes to an adapter, so hosts can move back at any time.
# Adding a catalog entry: one dependency block below + its line in the unit map of inputs.
# Mocks below only serve `validate`/`plan` of a not-yet-deployed env; `apply` requires real outputs.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  catalog  = local.env_vars.locals.catalog
}

terraform {
  source = "../../../../../modules//5-workloads/gateways/direct"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "corporate_email" {
  config_path = "../../services/corporate-email"
  mock_outputs = {
    service_name = "mock-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "income_verification" {
  config_path = "../../services/income-verification"
  mock_outputs = {
    service_name = "mock-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "legacy_dms" {
  config_path = "../../services/legacy-dms"
  mock_outputs = {
    service_name = "mock-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  project_id  = dependency.projects.outputs.gateway_project_id
  environment = local.env_vars.locals.environment
  region      = local.env_vars.locals.region

  # Catalog `unit` => that unit's outputs. MCP services live in the MCP project.
  mcp_services = {
    for name, entry in local.catalog : name => {
      project_id = dependency.projects.outputs.mcps_project_id
      service_name = {
        "services/corporate-email"     = dependency.corporate_email.outputs.service_name
        "services/income-verification" = dependency.income_verification.outputs.service_name
        "services/legacy-dms"          = dependency.legacy_dms.outputs.service_name
      }[entry.unit]
    } if entry.kind == "mcp"
  }

  agents = {
    for name, entry in local.catalog : name => {
      kind = entry.kind
      endpoint_url = {
        "agents/ai-coe-mortgage-specialist" = dependency.ai_coe_mortgage_specialist.outputs.endpoint_url
        "agents/cx-mortgage-orchestrator"   = dependency.cx_mortgage_orchestrator.outputs.endpoint_url
      }[entry.unit]
      timeout_sec = try(entry.timeout, 600)
    } if contains(["a2a", "adk"], entry.kind)
  }
}
