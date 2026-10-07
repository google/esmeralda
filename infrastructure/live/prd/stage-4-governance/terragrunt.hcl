# infrastructure/live/prd/stage-4-governance/terragrunt.hcl
# Layer 4: Central Agent Gateway (ACT + IAP), Agent Registry, Model Armor, telemetry, alerts.
# Depends only on layers 1-3; layer 5 workloads consume its gateway ID and CA bundle.
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//4-governance"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

dependency "projects" {
  config_path = "../stage-1-projects"
}

dependency "networking" {
  config_path = "../stage-2-networking"
}

dependency "security" {
  config_path = "../stage-3-security"
}

inputs = {
  environment           = local.env_vars.locals.environment
  governance_project_id = dependency.projects.outputs.governance_project_id
  region                = local.env_vars.locals.region
  spoke_project_ids = [
    dependency.projects.outputs.net_host_project_id,
    dependency.projects.outputs.gateway_project_id,
    dependency.projects.outputs.mcps_project_id,
    dependency.projects.outputs.a2a_project_id,
    dependency.projects.outputs.root_project_id
  ]
  alert_email_address          = "esmeralda.secops@google.com"
  runaway_loop_token_threshold = 50000
  monitoring_config            = local.env_vars.locals.monitoring_config
  # BigQuery views query log tables that only exist once agents have served traffic, so they
  # can't be created on a fresh env. `make deploy-governance-views` sets ENABLE_ANALYTICS_VIEWS=true;
  # set enable_analytics_views = true in env.yaml afterwards to keep them on plain re-applies.
  enable_analytics_views = tobool(get_env("ENABLE_ANALYTICS_VIEWS", tostring(lookup(local.env_vars.locals, "enable_analytics_views", false))))

  # Central Agent Gateway
  enable_agent_gateway = true
  subnet_self_link     = dependency.networking.outputs.psc_subnet_id
  net_host_project_id  = dependency.projects.outputs.net_host_project_id
  vpc_name             = element(split("/", dependency.networking.outputs.network_id), 4)
  internal_root_ca_pem = dependency.security.outputs.internal_ca_cert_pem
  agent_invoker_sa_emails = [
    dependency.security.outputs.root_agent_sa_email,
    dependency.security.outputs.a2a_agent_sa_email,
    dependency.security.outputs.test_vm_sa_email
  ]
  agent_project_ids = [
    dependency.projects.outputs.a2a_project_id,
    dependency.projects.outputs.root_project_id
  ]
}
