# infrastructure/live/prd/layer-5-workloads/apigee/platform/terragrunt.hcl
# Layer 5 Apigee platform unit: non-peering PSC Apigee X organization (or BYO), instance,
# environment, environment group, northbound PSC NEG + INTERNAL_MANAGED backend service,
# optional southbound endpoint attachment + target server, and proxy runtime service account.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  env      = local.env_vars.locals
}

terraform {
  source = "../../../../../modules//5-workloads/apigee/platform"
}

# Skipped by `run --all` when Apigee is disabled in env.yaml.
exclude {
  if      = !try(local.env.governance.apigee.enabled, false) && !contains(try(local.env.gateways.enabled, []), "apigee")
  actions = ["all"]
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
  mock_outputs = {
    gateway_project_id  = "esm-prd-gateway-mock"
    net_host_project_id = "esm-prd-net-host-mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
  mock_outputs = {
    network_id        = "projects/mock/global/networks/mock"
    subnet_id         = "projects/mock/regions/us-central1/subnetworks/mock"
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

inputs = {
  project_id  = dependency.projects.outputs.gateway_project_id
  environment = local.env.environment
  region      = local.env.region
  vpc_id      = dependency.networking.outputs.network_id
  subnet_id   = dependency.networking.outputs.subnet_id

  apigee_env_name  = "esmeralda-${local.env.environment}"
  environment_type = try(local.env.governance.apigee.environment_type, "BASE")
  billing_type     = try(local.env.governance.apigee.billing_type, "PAYG")
  enable_analytics = try(local.env.governance.apigee.enable_analytics, false)
  enable_cmek      = try(local.env.governance.apigee.enable_cmek, false)

  enable_advanced_api_ops = try(local.env.governance.apigee.enable_advanced_api_ops, false)

  byo_apigee_org                       = try(local.env.governance.apigee.byo, try(local.env.byo_apigee, false))
  existing_org_id                      = try(local.env.governance.apigee.existing_org_id, null)
  existing_instance_service_attachment = try(local.env.governance.apigee.existing_instance_service_attachment, null)

  envgroup_hostnames = [
    "partner.${dependency.networking.outputs.entrypoint_domain}",
    "apigee.${dependency.networking.outputs.entrypoint_domain}",
  ]

  consumer_accept_list = [
    dependency.projects.outputs.gateway_project_id,
    dependency.projects.outputs.net_host_project_id,
  ]
}
