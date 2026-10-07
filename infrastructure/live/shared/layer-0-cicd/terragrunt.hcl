# infrastructure/live/shared/layer-0-cicd/terragrunt.hcl
# Layer 0: shared CI/CD project, dev + release Artifact Registry repositories,
# builder and promoter service accounts. Consumed by every env via `dependency "cicd"`.
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//0-cicd"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  project_prefix  = local.env_vars.locals.project_prefix
  region          = local.env_vars.locals.region
  billing_account = local.env_vars.locals.billing_account
  org_id          = local.env_vars.locals.org_id
  folder_id       = local.env_vars.locals.folder_id
}
