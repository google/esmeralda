# infrastructure/live/prd/layer-5-workloads/apigee/capabilities/llm-gateway/terragrunt.hcl
# Agent -> model hop through Apigee (governance.capabilities.model_gateway = "apigee"): the
# llm-gateway proxy verifies each agent's ID token, meters and quotas tokens per caller, and
# forwards to Vertex AI. Agents get ESMERALDA_MODEL_GATEWAY_URL from their own units.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env = read_terragrunt_config(find_in_parent_folders("env.yaml")).locals

  # Source of truth for who may call models: the callers of the catalog's `llm-gateway` target
  # (managed: false, https://apigee.{domain}/llm), compiled into catalog_iap_egress as
  # agents/<name>:agent_identity_principal references. The same list is the target's
  # roles/iap.egressor policy at the Central Agent Gateway (iap-egress unit).
  caller_refs = try(local.env.catalog_iap_egress.endpoints["llm-gateway"], [])
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
    apigee_env_name        = "esmeralda-prd"
    apigee_env_id          = "organizations/mock-org/environments/esmeralda-prd"
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
    cx_agents_project_id     = "esm-prd-cx-agents-mock"
    ai_coe_agents_project_id = "esm-prd-ai-coe-agents-mock"
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

# Agents call models as their own Agent Identity (unbound ID tokens, audience https://apigee.<domain>).
dependency "cx_mortgage_orchestrator" {
  config_path = "../../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-cx-mortgage-orchestrator"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-ai-coe-mortgage-specialist"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  org_id          = dependency.platform.outputs.org_id
  apigee_env_name = dependency.platform.outputs.apigee_env_name
  apigee_env_id   = dependency.platform.outputs.apigee_env_id
  service_account = dependency.platform.outputs.proxy_runtime_sa_email

  # The module runs from .terragrunt-cache, where the repo-relative default path doesn't exist.
  bundle_source_dir = "${get_repo_root()}/apps/apigee/proxies/llm-gateway"

  gateway_host = "apigee.${dependency.networking.outputs.entrypoint_domain}"

  # agents/<name>:agent_identity_principal -> unit output; an unmapped reference fails the plan
  # (add the agent's dependency block above).
  agent_principals = {
    for ref in local.caller_refs :
    trimprefix(split(":", ref)[0], "agents/") => {
      "agents/cx-mortgage-orchestrator:agent_identity_principal"   = dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal
      "agents/ai-coe-mortgage-specialist:agent_identity_principal" = dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal
    }[ref]
  }
  # Service account callers (Google ID tokens): the test VM, for probes.
  allowed_callers = compact([try(dependency.security.outputs.test_vm_sa_email, "")])
  model_projects = [
    dependency.projects.outputs.cx_agents_project_id,
    dependency.projects.outputs.ai_coe_agents_project_id,
  ]
  allowed_locations = distinct([local.env.region, "global"])
}
