# infrastructure/live/prd/layer-5-workloads/services/esmeralda-ui/terragrunt.hcl
# Optional Layer 5 Esmeralda UI console on Cloud Run (Direct IAP + optional Identity Platform / GCIP).
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  env      = local.env_vars.locals
  ui_cfg   = try(local.env.esmeralda_ui, {})
  digest   = try(local.env.image_digests["esmeralda-ui"], "")
}

terraform {
  source = "../../../../../modules//5-workloads/services/esmeralda-ui"
}

# Off by default unless esmeralda_ui.enabled = true in env.yaml.
exclude {
  if      = !try(local.ui_cfg.enabled, false)
  actions = ["all"]
}

dependency "cicd" {
  config_path = "../../../../shared/layer-0-cicd"
  mock_outputs = {
    release_repository_url = "us-central1-docker.pkg.dev/mock/esmeralda-release"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
  mock_outputs = {
    mcps_project_id       = "esm-prd-mcps-mock"
    governance_project_id = "esm-prd-governance-mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
  mock_outputs = {
    network_id        = "projects/mock/global/networks/mock"
    subnet_id         = "projects/mock/regions/us-central1/subnetworks/mock"
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  project_id             = dependency.projects.outputs.mcps_project_id
  governance_project_id  = dependency.projects.outputs.governance_project_id
  region                 = local.env.region
  environment            = local.env.environment
  network_id             = dependency.networking.outputs.network_id
  subnet_id              = dependency.networking.outputs.subnet_id
  entrypoint_domain      = dependency.networking.outputs.entrypoint_domain
  container_image        = local.digest != "" ? "${dependency.cicd.outputs.release_repository_url}/esmeralda-ui@${local.digest}" : "${dependency.cicd.outputs.release_repository_url}/esmeralda-ui:${local.env.container_tag}"
  iap_enabled            = try(local.ui_cfg.iap_enabled, true)
  iap_accessors          = try(local.ui_cfg.iap_accessors, [])
  invoker_members        = try(local.ui_cfg.invoker_members, [])
  identity_platform      = try(local.ui_cfg.identity_platform, { enabled = false, tenant_ids = [], login_page_uri = "" })
  allow_persona_override = try(local.ui_cfg.allow_persona_override, false)
}
