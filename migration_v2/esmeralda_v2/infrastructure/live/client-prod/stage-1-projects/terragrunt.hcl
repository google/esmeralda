# infrastructure/live/client-prod/stage-1-projects/terragrunt.hcl
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
  billing_account        = "012345-6789AB-CDEF01" # Corporate billing account ID
  project_prefix         = local.env_vars.locals.project_prefix
  environment            = local.env_vars.locals.environment
  byo_net_host_project   = local.env_vars.locals.byo_net_host_project
  byo_gateway_project    = local.env_vars.locals.byo_gateway_project
  byo_governance_project = local.env_vars.locals.byo_governance_project
}
