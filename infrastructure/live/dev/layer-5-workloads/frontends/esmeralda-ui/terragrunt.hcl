# infrastructure/live/dev/layer-5-workloads/frontends/esmeralda-ui/terragrunt.hcl
# Layer 5 frontend: the read-only Esmeralda console (apps/frontends/esmeralda-ui) on Cloud Run in the
# gateway project, behind IAP on the private entry point as <host>.<domain> (env.yaml frontends).
# It owns a serverless NEG + regional backend service (backend_service_id) but no load balancer,
# certificate or DNS record: the entrypoint unit routes the host to it. Off unless
# frontends.esmeralda_ui.enabled; enabling it = apply this unit, then the entrypoint.
# Mocks below only serve `validate`/`plan` of a not-yet-deployed env; `apply` requires real outputs.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  frontend = try(local.env_vars.locals.frontends.esmeralda_ui, {})

  # Viewer groups: env.yaml (reviewed, the place for forks) plus ESMERALDA_UI_IAP_ACCESSORS from the
  # user-local env file (comma-separated), for environments whose group must stay out of the repo.
  env_accessors = [for a in split(",", get_env("ESMERALDA_UI_IAP_ACCESSORS", "")) : trimspace(a) if trimspace(a) != ""]

  # Image: pinned by digest when the env lists it in image_digests (prd: release/prd.yaml,
  # written by `make promote`), otherwise <image>:<container_tag> (resolved to a digest by the module).
  image_name   = "esmeralda-ui"
  image_digest = lookup(lookup(local.env_vars.locals, "image_digests", {}), local.image_name, "")
  image_ref    = local.image_digest != "" ? "${local.image_name}@${local.image_digest}" : "${local.image_name}:${local.env_vars.locals.container_tag}"
}

terraform {
  source = "../../../../../modules//5-workloads/frontends/esmeralda-ui"
}

# Skipped by `run --all` (and `esmeralda deploy --layer 5`) unless frontends.esmeralda_ui.enabled.
exclude {
  if      = !try(local.env_vars.locals.frontends.esmeralda_ui.enabled, false)
  actions = ["all"]
}

dependency "cicd" {
  config_path = "../../../../shared/layer-0-cicd"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
  mock_outputs = {
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

dependency "governance" {
  config_path = "../../../layer-4-governance"
  mock_outputs = {
    governance_dataset_id = "esmeralda_telemetry_logs_mock"
    usage_dataset_id      = "esmeralda_usage_mock"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

inputs = {
  project_id      = dependency.projects.outputs.gateway_project_id
  environment     = local.env_vars.locals.environment
  region          = local.env_vars.locals.region
  domain          = dependency.networking.outputs.entrypoint_domain
  host_label      = try(local.frontend.host, "console")
  container_image = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/${local.image_ref}"

  # Read-only data: the layer-4 telemetry views (and the usage tables they query) in the governance project.
  bq_project           = dependency.projects.outputs.governance_project_id
  telemetry_dataset_id = dependency.governance.outputs.governance_dataset_id
  usage_dataset_id     = try(dependency.governance.outputs.usage_dataset_id, "")

  iap_accessors          = distinct(concat(try(local.frontend.iap_accessors, []), local.env_accessors))
  allow_domain_accessors = try(local.frontend.allow_domain_accessors, false)
}
