# infrastructure/live/prd/layer-5-workloads/apigee/products/terragrunt.hcl
# Layer 5 Apigee API products & partner consumer applications unit.
# Provisions catalog-driven API products (with server-side MCP tool allowlists and LLM token
# quota attributes), partner developer apps, and Secret Manager consumer key synchronization.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  env      = local.env_vars.locals
}

terraform {
  source = "../../../../../modules//5-workloads/apigee/products"
}

# Skipped by `run --all` when Apigee is disabled in env.yaml.
exclude {
  if      = !try(local.env.governance.apigee.enabled, false) && !contains(try(local.env.gateways.enabled, []), "apigee")
  actions = ["all"]
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
  mock_outputs = {
    gateway_project_id = "esm-prd-gateway-mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "security" {
  config_path = "../../../layer-3-security"
  mock_outputs = {
    test_vm_sa_email = "sa-test-vm-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "platform" {
  config_path = "../platform"
  mock_outputs = {
    org_id          = "organizations/esm-prd-gateway-mock"
    apigee_env_name = "esmeralda-prd"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

inputs = {
  org_id          = dependency.platform.outputs.org_id
  project_id      = dependency.projects.outputs.gateway_project_id
  environment     = local.env.environment
  apigee_env_name = dependency.platform.outputs.apigee_env_name

  secret_accessor_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
  ]

  products = {
    esmeralda-partner-standard = {
      display_name        = "Esmeralda Partner Standard"
      description         = "Standard partner tier for Esmeralda mortgage orchestrator and read-only MCP tools."
      approval_type       = "auto"
      proxies             = ["partner-agents-proxy", "partner-mcp-proxy"]
      allowed_paths       = ["/", "/*"]
      allowed_mcp_tools   = ["verify_income", "fetch_credit_report"]
      llm_quota_limit     = 50000
      llm_quota_interval  = 1
      llm_quota_time_unit = "hour"
    }
    esmeralda-partner-premium = {
      display_name        = "Esmeralda Partner Premium"
      description         = "Premium partner tier with full MCP tool access and higher LLM token quota."
      approval_type       = "auto"
      proxies             = ["partner-agents-proxy", "partner-mcp-proxy"]
      allowed_paths       = ["/", "/*"]
      allowed_mcp_tools   = ["verify_income", "fetch_credit_report", "query_document_metadata", "send_applicant_notification"]
      llm_quota_limit     = 500000
      llm_quota_interval  = 1
      llm_quota_time_unit = "hour"
    }
  }

  consumers = {
    partner-smoke-test = {
      display_name    = "Esmeralda Partner Conformance Smoke Test"
      developer_email = "partner-smoke-test@partner.esmeralda.internal"
      products        = ["esmeralda-partner-standard"]
      owner_identity  = dependency.security.outputs.test_vm_sa_email
    }
  }
}
