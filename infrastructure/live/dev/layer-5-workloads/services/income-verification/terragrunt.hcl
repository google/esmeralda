# infrastructure/live/dev/layer-5-workloads/services/income-verification/terragrunt.hcl
# Layer 5: MCP server on Cloud Run + its entry in the central governance Agent Registry.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../../modules//5-workloads/services/income-verification"
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

# Apigee partner front door (partner_access = "apigee"): its proxy runtime service account is the
# identity of the catalog caller apigee-partner-mcp.
dependency "apigee" {
  config_path = "../../apigee/platform"
  enabled     = local.apigee_partner
  mock_outputs = {
    proxy_runtime_sa_email = "sa-apigee-proxy-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  gateways = local.env_vars.locals.gateways
  # This service's catalog entry (env.yaml catalog), keyed by the unit's directory name.
  name    = basename(get_terragrunt_dir())
  callers = try(local.env_vars.locals.catalog[local.name].callers, [])
  apigee_partner = (
    try(local.env_vars.locals.governance.apigee.enabled, false) &&
    try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee"
  )

  # Image: pinned by digest when the env lists it in image_digests (prd: release/prd.yaml,
  # written by `make promote`), otherwise <image>:<container_tag> (resolved to a digest by the module).
  image_name   = "income-verification-api"
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
  tools_spec_path       = "${get_repo_root()}/apps/services/income-verification/tools.json"
  internal_hostname     = "${local.name}.${dependency.networking.outputs.entrypoint_domain}"

  # roles/run.invoker: the service accounts of the catalog callers (apigee-partner-mcp: the Apigee
  # proxy runtime service account, only with partner_access = "apigee"); when an agent calls, also the
  # identity agents call out as (SERVICE_ACCOUNT_EMAIL, the shared MCP invoker service account); and
  # the identity of each enabled adapter, which calls with its own token. direct adds none: callers
  # reach the service with their own ID token.
  invoker_service_accounts = distinct(compact(concat(
    [for caller in local.callers : (
      caller == "apigee-partner-mcp"
      ? (local.apigee_partner ? dependency.apigee.outputs.proxy_runtime_sa_email : "")
      : dependency.security.outputs["${replace(caller, "-", "_")}_sa_email"]
    )],
    length([for caller in local.callers : caller if !contains(["test-vm", "apigee-partner-mcp"], caller)]) > 0 ? [dependency.security.outputs.mcp_invoker_sa_email] : [],
    contains(local.gateways.enabled, "envoy") ? [try(dependency.security.outputs.envoy_sa_email, "")] : [],
  )))
}
