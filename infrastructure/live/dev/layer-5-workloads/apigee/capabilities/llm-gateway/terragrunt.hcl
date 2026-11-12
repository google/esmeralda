# infrastructure/live/dev/layer-5-workloads/apigee/capabilities/llm-gateway/terragrunt.hcl
# Agent -> model hop through Apigee (governance.capabilities.model_gateway = "apigee"): the
# llm-gateway proxy verifies each agent's ID token, meters and quotas tokens per caller, and
# forwards to Vertex AI. Agents get ESMERALDA_MODEL_GATEWAY_URL from their own units.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env = read_terragrunt_config(find_in_parent_folders("env.yaml")).locals

  # Agents in the catalog (kind a2a / adk) call models; their service accounts are the verified
  # principals of the ID tokens they send (plus the test VM, for probes).
  agent_names = [for name, entry in local.env.catalog : name if contains(["a2a", "adk"], try(entry.kind, ""))]
}

terraform {
  source = "../../../../../../modules//5-workloads/apigee/capabilities/llm-gateway"
}

exclude {
  if      = !(try(local.env.governance.apigee.enabled, false) && try(local.env.governance.capabilities.model_gateway, "none") == "apigee")
  actions = ["all"]
}

dependency "platform" {
  config_path = "../../platform"
  mock_outputs = {
    org_id                 = "organizations/mock-org"
    apigee_env_name        = "esmeralda-dev"
    apigee_env_id          = "organizations/mock-org/environments/esmeralda-dev"
    proxy_runtime_sa_email = "sa-apigee-proxy-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

# Ordering only: usage-metering provisions the data collectors DC-capture-llm-metrics writes to.
dependency "usage_metering" {
  config_path                             = "../usage-metering"
  skip_outputs                            = true
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "projects" {
  config_path = "../../../../layer-1-projects"
  mock_outputs = {
    cx_agents_project_id     = "esm-dev-cx-agents-mock"
    ai_coe_agents_project_id = "esm-dev-ai-coe-agents-mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "networking" {
  config_path = "../../../../layer-2-networking"
  mock_outputs = {
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "security" {
  config_path = "../../../../layer-3-security"
  mock_outputs = {
    test_vm_sa_email = "sa-test-vm-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

inputs = {
  org_id          = dependency.platform.outputs.org_id
  apigee_env_name = dependency.platform.outputs.apigee_env_name
  apigee_env_id   = dependency.platform.outputs.apigee_env_id
  service_account = dependency.platform.outputs.proxy_runtime_sa_email

  gateway_host = "apigee.${dependency.networking.outputs.entrypoint_domain}"
  allowed_callers = distinct(compact(concat(
    [for name in local.agent_names : try(dependency.security.outputs["${replace(name, "-", "_")}_sa_email"], "")],
    [try(dependency.security.outputs.test_vm_sa_email, "")],
  )))
  model_projects = [
    dependency.projects.outputs.cx_agents_project_id,
    dependency.projects.outputs.ai_coe_agents_project_id,
  ]
  allowed_locations = distinct([local.env.region, "global"])
}
