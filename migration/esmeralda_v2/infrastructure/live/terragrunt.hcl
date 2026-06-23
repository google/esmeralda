# infrastructure/live/terragrunt.hcl
locals {
  env_vars    = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  region      = local.env_vars.locals.region
  prefix      = local.env_vars.locals.project_prefix
  environment = local.env_vars.locals.environment

  # Check if custom remote state configurations are specified in env.yaml
  state_project = lookup(local.env_vars.locals, "state_project", "")
  state_bucket  = lookup(local.env_vars.locals, "state_bucket", "")

  # If both project and bucket are configured, use "gcs", otherwise default to "local"
  backend_type = (local.state_project != "" && local.state_bucket != "") ? "gcs" : "local"
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

# Dynamic, unified state backend (uses GCS if project/bucket configured, local state otherwise)
remote_state {
  backend = local.backend_type
  config = local.backend_type == "gcs" ? {
    bucket   = local.state_bucket
    prefix   = "${path_relative_to_include()}/terraform.tfstate"
    project  = local.state_project
    location = local.region
  } : {
    path = "${get_parent_terragrunt_dir()}/.local_states/${path_relative_to_include()}/terraform.tfstate"
  }
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
}
