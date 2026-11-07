# infrastructure/live/prd/layer-5-workloads/data/cloud-sql/terragrunt.hcl
# Layer 5 (stateful data): Cloud SQL PostgreSQL task store for A2A agents, encrypted with Layer 3 CMEK.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../../modules//5-workloads/data/cloud-sql"
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
  env_cfg  = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  env_vars = merge(local.env_cfg.locals, { locals = local.env_cfg.locals })
}

inputs = {
  enabled                  = try(local.env_vars.database_product, "cloud-sql") == "cloud-sql"
  project_id               = dependency.projects.outputs.ai_coe_agents_project_id
  environment              = local.env_vars.environment
  region                   = local.env_vars.region
  vpc_id                   = dependency.networking.outputs.network_id
  subnet_id                = dependency.networking.outputs.subnet_id
  agent_service_account    = dependency.security.outputs.ai_coe_mortgage_specialist_sa_email
  database_key_id          = dependency.security.outputs.database_key_id
  admin_password_secret_id = dependency.security.outputs.db_password_secret_name
  database_name            = "a2a_tasks"
}
