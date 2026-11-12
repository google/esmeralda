# infrastructure/live/prd/layer-5-workloads/apigee/capabilities/partner-mcp/terragrunt.hcl
# partner-mcp proxy + its southbound path to the internal entry point (PSC service attachment on the
# entry point forwarding rule, Apigee endpoint attachment, internal CA truststore, MCP target server).
# Excluded while prd keeps partner_access = "none".
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env = read_terragrunt_config(find_in_parent_folders("env.yaml")).locals
}

terraform {
  source = "../../../../../../modules//5-workloads/apigee/capabilities/partner-mcp"
}

exclude {
  if      = !(try(local.env.governance.apigee.enabled, false) && try(local.env.governance.capabilities.partner_access, "none") == "apigee")
  actions = ["all"]
}

# The service attachment targets the entry point forwarding rule: apply after the entry point.
dependencies {
  paths = ["../../../entrypoint"]
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

dependency "projects" {
  config_path = "../../../../layer-1-projects"
  mock_outputs = {
    gateway_project_id  = "esm-prd-gateway-mock"
    net_host_project_id = "esm-prd-net-host-mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "networking" {
  config_path = "../../../../layer-2-networking"
  mock_outputs = {
    network_id        = "projects/mock/global/networks/mock"
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "security" {
  config_path = "../../../../layer-3-security"
  mock_outputs = {
    internal_ca_cert_pem = "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----\n"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

# Ordering only: FC-Cross-Cloud calls the cross-cloud shared flow, which Apigee requires to be
# deployed in the environment before this proxy (Layer 4 enforces cross_cloud = "apigee").
dependency "cross_cloud" {
  config_path                             = "../cross-cloud"
  skip_outputs                            = true
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  org_id            = dependency.platform.outputs.org_id
  apigee_env_name   = dependency.platform.outputs.apigee_env_name
  apigee_env_id     = dependency.platform.outputs.apigee_env_id
  service_account   = dependency.platform.outputs.proxy_runtime_sa_email
  bundle_source_dir = "${get_repo_root()}/apps/apigee/proxies/partner-mcp"
  apex_domain       = dependency.networking.outputs.entrypoint_domain

  southbound_enabled   = true
  project_id           = dependency.projects.outputs.gateway_project_id
  region               = local.env.region
  environment          = local.env.environment
  network_project_id   = dependency.projects.outputs.net_host_project_id
  network_id           = dependency.networking.outputs.network_id
  internal_ca_cert_pem = dependency.security.outputs.internal_ca_cert_pem
}
