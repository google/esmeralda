# infrastructure/live/prd/stage-2-networking/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//2-networking"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

# Skips execution entirely if networking is pre-configured
skip = local.env_vars.locals.byo_networking
