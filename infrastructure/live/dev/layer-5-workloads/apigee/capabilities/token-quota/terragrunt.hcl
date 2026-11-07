# infrastructure/live/dev/layer-5-workloads/apigee/capabilities/token-quota/terragrunt.hcl
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env = read_terragrunt_config(find_in_parent_folders("env.yaml")).locals
}

terraform {
  source = "../../../../../../modules//5-workloads/apigee/capabilities/token-quota"
}

exclude {
  if      = !(try(local.env.governance.apigee.enabled, false) && try(local.env.governance.capabilities.token_quota, "none") == "apigee")
  actions = ["all"]
}

dependency "platform" {
  config_path = "../../platform"
  mock_outputs = {
    org_id                 = "organizations/mock-org"
    apigee_env_name        = "esmeralda-dev"
    proxy_runtime_sa_email = "sa-apigee-proxy-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  org_id            = dependency.platform.outputs.org_id
  apigee_env_name   = dependency.platform.outputs.apigee_env_name
  service_account   = dependency.platform.outputs.proxy_runtime_sa_email
  bundle_source_dir = "${get_repo_root()}/apps/apigee/sharedflows/token-quota"
}
