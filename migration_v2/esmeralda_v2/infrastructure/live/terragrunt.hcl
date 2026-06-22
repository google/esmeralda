# infrastructure/live/terragrunt.hcl
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  region   = local.env_vars.locals.region
  prefix   = local.env_vars.locals.project_prefix
}

# Generate Google provider blocks automatically
generate "provider" {
  path      = "provider.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<EOF
provider "google" {
  region = "${local.region}"
}

provider "google-beta" {
  region = "${local.region}"
}
EOF
}

# Dynamic, unified GCS remote state backend configuration
remote_state {
  backend = "gcs"
  config = {
    bucket   = "tf-state-${local.prefix}-${local.env_vars.locals.environment}"
    prefix   = "${path_relative_to_include()}/terraform.tfstate"
    project  = "${local.prefix}-tf-admin"
    location = local.region
  }
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
}
