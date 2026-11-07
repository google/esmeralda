# infrastructure/live/prd/layer-5-workloads/services/corporate-email/terragrunt.hcl
# Layer 5: MCP server on Cloud Run + its entry in the central governance Agent Registry.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../../modules//5-workloads/services/corporate-email"
}

dependency "cicd" {
  config_path = "../../../../shared/layer-0-cicd"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
}

dependency "security" {
  config_path = "../../../layer-3-security"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))

  # Image: pinned by digest when the env lists it in image_digests (prd: release/prd.yaml,
  # written by `make promote`), otherwise <image>:<container_tag> (resolved to a digest by the module).
  image_name   = "corporate-email"
  image_digest = lookup(lookup(local.env_vars.locals, "image_digests", {}), local.image_name, "")
  image_ref    = local.image_digest != "" ? "${local.image_name}@${local.image_digest}" : "${local.image_name}:${local.env_vars.locals.container_tag}"
}

inputs = {
  project_id            = dependency.projects.outputs.mcps_project_id
  governance_project_id = dependency.projects.outputs.governance_project_id
  environment           = local.env_vars.locals.environment
  region                = local.env_vars.locals.region
  network_id            = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  container_image       = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/${local.image_ref}"
  tools_spec_path       = "${get_repo_root()}/apps/services/corporate-email/tools.json"
  # The Envoy gateway's identity is added when envoy is in gateways.enabled (env.yaml).
  invoker_service_accounts = concat([
    dependency.security.outputs.cx_mortgage_orchestrator_sa_email,
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.kong_sa_email
  ], contains(local.env_vars.locals.gateways.enabled, "envoy") ? [try(dependency.security.outputs.envoy_sa_email, null)] : [])
}
