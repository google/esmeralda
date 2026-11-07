# infrastructure/live/prd/layer-5-workloads/entrypoint/terragrunt.hcl
# Layer 5 entry point: URL map + HTTPS proxy + forwarding rule for *.esmeralda.internal.
# Serves every catalog host (env.yaml catalog) through gateways.default, and the hosts in
# gateways.host_rules through their backend: "direct" (built-in, gateways/direct unit) or an enabled
# adapter (gateways.enabled). The reserved IP + DNS records come from layer 2, the certificate from layer 3.
# Applied after the backends: the direct unit always, plus one dependency block per known adapter,
# enabled only when it is listed in gateways.enabled. Adding an adapter (e.g. apigee) = one more
# dependency block below plus its entry in inputs.adapters.
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

# --- Built-in direct backends (modules/5-workloads/gateways/direct): per-host backends ---

dependency "direct" {
  config_path = "../gateways/direct"
  mock_outputs = {
    hosts = {
      for name, entry in local.env_vars.locals.catalog : name => {
        backend_service_id = "projects/mock/regions/us-central1/backendServices/mock-direct-${name}"
        host_rewrite       = null
        timeout_sec        = null
        path_rewrites      = []
      }
    }
    default_backend_service_id = "projects/mock/regions/us-central1/backendServices/mock-direct"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

# --- Adapters (modules/5-workloads/gateways/*): each outputs backend_service_id ---

dependency "envoy" {
  config_path = "../gateways/envoy"
  enabled     = contains(local.env_vars.locals.gateways.enabled, "envoy")
  mock_outputs = {
    backend_service_id = "projects/mock/regions/us-central1/backendServices/mock-envoy"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "apigee" {
  config_path = "../apigee/platform"
  enabled     = contains(local.env_vars.locals.gateways.enabled, "apigee") || (try(local.env_vars.locals.governance.apigee.enabled, false) && try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee")
  mock_outputs = {
    backend_service_id = "projects/mock/regions/us-central1/backendServices/mock-apigee"
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

  # Catalog hosts and how each is probed; extra attributes (unit, callers, timeout) stay in env.yaml.
  catalog = {
    for name, entry in local.env_vars.locals.catalog : name => {
      kind  = entry.kind
      token = entry.token
      probe = try(entry.probe, "none")
    }
  }

  direct_hosts                      = dependency.direct.outputs.hosts
  direct_default_backend_service_id = dependency.direct.outputs.default_backend_service_id

  # name => backend_service_id of every enabled adapter (a disabled dependency is never read).
  # Validated by the module: default and every host_rules value must be direct or an enabled adapter.
  adapters = {
    for name, id in {
      envoy  = try(dependency.envoy.outputs.backend_service_id, null)
      apigee = try(dependency.apigee.outputs.backend_service_id, null)
    } : name => id if contains(local.gateways.enabled, name) || (name == "apigee" && try(local.env_vars.locals.governance.apigee.enabled, false) && try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee")
  }
  default_backend = local.gateways.default
  host_rules = merge(
    try(local.env_vars.locals.governance.apigee.enabled, false) && try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee" ? { "partner" = "apigee" } : {},
    local.gateways.host_rules
  )
}
