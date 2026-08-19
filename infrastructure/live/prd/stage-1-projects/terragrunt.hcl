# infrastructure/live/prd/stage-1-projects/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//1-projects"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  billing_account        = local.env_vars.locals.billing_account
  org_id                 = local.env_vars.locals.org_id
  folder_id              = lookup(local.env_vars.locals, "folder_id", "")
  project_prefix         = local.env_vars.locals.project_prefix
  environment            = local.env_vars.locals.environment
  byo_net_host_project   = local.env_vars.locals.byo_net_host_project
  byo_gateway_project    = local.env_vars.locals.byo_gateway_project
  byo_governance_project = local.env_vars.locals.byo_governance_project
  byo_cicd_project       = lookup(local.env_vars.locals, "byo_cicd_project", false)

  existing_net_host_project   = lookup(local.env_vars.locals, "existing_net_host_project", "")
  existing_gateway_project    = lookup(local.env_vars.locals, "existing_gateway_project", "")
  existing_governance_project = lookup(local.env_vars.locals, "existing_governance_project", "")
  existing_cicd_project       = lookup(local.env_vars.locals, "existing_cicd_project", "")
}
