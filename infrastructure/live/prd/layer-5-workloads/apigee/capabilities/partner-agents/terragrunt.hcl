# infrastructure/live/prd/layer-5-workloads/apigee/capabilities/partner-agents/terragrunt.hcl
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env = read_terragrunt_config(find_in_parent_folders("env.yaml")).locals
}

terraform {
  source = "../../../../../../modules//5-workloads/apigee/capabilities/partner-agents"
}

exclude {
  if      = !(try(local.env.governance.apigee.enabled, false) && try(local.env.governance.capabilities.partner_access, "none") == "apigee")
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

dependency "cx_mortgage_orchestrator" {
  config_path = "../../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    engine_id = "projects/mock/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  org_id                 = dependency.platform.outputs.org_id
  apigee_env_name        = dependency.platform.outputs.apigee_env_name
  apigee_env_id          = dependency.platform.outputs.apigee_env_id
  service_account        = dependency.platform.outputs.proxy_runtime_sa_email
  orchestrator_engine_id = dependency.cx_mortgage_orchestrator.outputs.engine_id
  bundle_source_dir      = "${get_repo_root()}/apps/apigee/proxies/partner-agents"
}
