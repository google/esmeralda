# infrastructure/live/dev/layer-3-security/terragrunt.hcl
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../modules//3-security"
}

dependency "cicd" {
  config_path = "../../shared/layer-0-cicd"
}

dependency "projects" {
  config_path = "../layer-1-projects"
}

locals {
  env_vars       = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  byo_networking = lookup(local.env_vars.locals, "byo_networking", false)
}

dependency "networking" {
  config_path = "../layer-2-networking"

  # Avoid running output lookups on skipped modules
  skip_outputs = local.byo_networking

  # Satisfy parser during evaluation with mock variables
  mock_outputs = {
    subnet_id         = local.env_vars.locals.existing_subnet_id
    dns_zone_dns_name = "esmeralda.internal."
  }
}

inputs = {
  net_host_project_id = dependency.projects.outputs.net_host_project_id
  gateway_project_id  = dependency.projects.outputs.gateway_project_id
  # Shared layer-0 CI/CD: this env only gets repository-scoped reader access
  cicd_project_id        = dependency.cicd.outputs.cicd_project_id
  artifact_region        = dependency.cicd.outputs.region
  artifact_repository_id = lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_id : dependency.cicd.outputs.dev_repository_id
  mcps_project_id        = dependency.projects.outputs.mcps_project_id

  ai_coe_agents_project_id = dependency.projects.outputs.ai_coe_agents_project_id
  cx_agents_project_id     = dependency.projects.outputs.cx_agents_project_id
  governance_project_id    = dependency.projects.outputs.governance_project_id
  project_suffix           = dependency.projects.outputs.project_suffix

  region      = local.env_vars.locals.region
  environment = local.env_vars.locals.environment
  org_id      = lookup(local.env_vars.locals, "org_id", "")

  # Verified attribution probe identity (env.yaml attribution.unregistered_probe): in no catalog
  # entry and granted nothing, impersonable by the test VM only.
  enable_unregistered_probe = try(local.env_vars.locals.attribution.unregistered_probe, false)

  # Deprecated outbound-identity fallback (env.yaml legacy_shared_invoker): keeps the shared invoker's grants.
  legacy_shared_invoker = try(local.env_vars.locals.legacy_shared_invoker, false)

  backend_subnet_id = local.byo_networking ? local.env_vars.locals.existing_subnet_id : dependency.networking.outputs.subnet_id

  # Entry point certificate (<domain> + *.<domain>), derived from the layer-2 private zone
  internal_domain = trimsuffix(dependency.networking.outputs.dns_zone_dns_name, ".")

  governance_secrets_service_agent = dependency.projects.outputs.governance_secrets_service_agent
}

