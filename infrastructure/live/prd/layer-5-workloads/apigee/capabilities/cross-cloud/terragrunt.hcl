# infrastructure/live/prd/layer-5-workloads/apigee/capabilities/cross-cloud/terragrunt.hcl
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env = read_terragrunt_config(find_in_parent_folders("env.yaml")).locals
}

terraform {
  source = "../../../../../../modules//5-workloads/apigee/capabilities/cross-cloud"
}

exclude {
  if      = !(try(local.env.governance.apigee.enabled, false) && try(local.env.governance.capabilities.cross_cloud, "agent_gateway") == "apigee")
  actions = ["all"]
}

dependency "platform" {
  config_path = "../../platform"
  mock_outputs = {
    org_id          = "organizations/mock-org"
    apigee_env_name = "esmeralda-prd"
    apigee_env_id   = "organizations/mock-org/environments/esmeralda-prd"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  org_id          = dependency.platform.outputs.org_id
  apigee_env_name = dependency.platform.outputs.apigee_env_name
  apigee_env_id   = dependency.platform.outputs.apigee_env_id
}
