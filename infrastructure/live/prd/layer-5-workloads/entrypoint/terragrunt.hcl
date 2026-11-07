# infrastructure/live/prd/layer-5-workloads/entrypoint/terragrunt.hcl
# Layer 5 entry point: URL map + HTTPS proxy + forwarding rule for *.esmeralda.internal.
# Routes every host to gateways.default and the hosts in gateways.host_rules to their gateway
# (env.yaml). The reserved IP + DNS records come from layer 2, the certificate from layer 3.
# Applied after the gateways: one dependency block per known gateway, enabled only when the
# gateway is listed in gateways.enabled. Adding a gateway (e.g. envoy) = one more dependency
# block below plus its entry in inputs.backends.
# Mocks below only serve `validate`/`plan` before the inputs exist; `apply` requires real outputs.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  gateways = local.env_vars.locals.gateways
}

terraform {
  source = "../../../../modules//5-workloads/entrypoint"
}

dependency "projects" {
  config_path = "../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../layer-2-networking"
  mock_outputs = {
    network_id         = "projects/mock/global/networks/mock"
    subnet_id          = "projects/mock/regions/us-central1/subnetworks/mock"
    entrypoint_address = "10.0.1.254"
    entrypoint_domain  = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "security" {
  config_path = "../../layer-3-security"
  mock_outputs = {
    entrypoint_ssl_certificate_id = "projects/mock/regions/us-central1/sslCertificates/mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

# --- Gateways (modules/5-workloads/gateways/*): each outputs backend_service_id ---

dependency "kong" {
  config_path = "../gateways/kong"
  enabled     = contains(local.env_vars.locals.gateways.enabled, "kong")
  mock_outputs = {
    backend_service_id = "projects/mock/regions/us-central1/backendServices/mock-kong"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

inputs = {
  project_id  = dependency.projects.outputs.gateway_project_id
  environment = local.env_vars.locals.environment
  region      = local.env_vars.locals.region

  vpc_id             = dependency.networking.outputs.network_id
  subnet_id          = dependency.networking.outputs.subnet_id
  ip_address         = dependency.networking.outputs.entrypoint_address
  domain             = dependency.networking.outputs.entrypoint_domain
  ssl_certificate_id = dependency.security.outputs.entrypoint_ssl_certificate_id

  # name => backend_service_id of every enabled gateway (a disabled dependency is never read).
  # Validated by the module: default and every host_rules value must be an enabled gateway.
  backends = {
    for name, id in {
      kong = try(dependency.kong.outputs.backend_service_id, null)
    } : name => id if contains(local.gateways.enabled, name)
  }
  default_backend = local.gateways.default
  host_rules      = local.gateways.host_rules
}
