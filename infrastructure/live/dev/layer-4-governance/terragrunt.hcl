# infrastructure/live/dev/layer-4-governance/terragrunt.hcl
# Layer 4: Central Agent Gateway (ACT + IAP), Agent Registry, Model Armor, telemetry, alerts.
# Depends only on layers 1-3; layer 5 workloads consume its gateway ID and CA bundle.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../modules//4-governance"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  env      = local.env_vars.locals
}

dependency "projects" {
  config_path = "../layer-1-projects"
}

dependency "networking" {
  config_path = "../layer-2-networking"
}

dependency "security" {
  config_path = "../layer-3-security"
}

inputs = {
  environment           = local.env_vars.locals.environment
  governance_project_id = dependency.projects.outputs.governance_project_id
  region                = local.env_vars.locals.region
  spoke_project_ids = [
    dependency.projects.outputs.net_host_project_id,
    dependency.projects.outputs.gateway_project_id,
    dependency.projects.outputs.mcps_project_id,
    dependency.projects.outputs.ai_coe_agents_project_id,
    dependency.projects.outputs.cx_agents_project_id
  ]
  alert_email_address          = "esmeralda.secops@google.com"
  runaway_loop_token_threshold = 50000
  monitoring_config            = local.env_vars.locals.monitoring_config
  governance_config            = try(local.env.governance, {})
  model_armor_config           = try(local.env.governance.model_armor, {})

  # Usage attribution floor: the team that owns each workload project (catalog teams.yaml keys).
  # Workload usage records logged from these projects are billed to that team until callers are verified.
  principal_teams = [
    { principal = dependency.projects.outputs.cx_agents_project_id, principal_type = "gcp_project", team = "cx" },
    { principal = dependency.projects.outputs.ai_coe_agents_project_id, principal_type = "gcp_project", team = "ai-coe" },
    { principal = dependency.projects.outputs.mcps_project_id, principal_type = "gcp_project", team = "ai-coe" },
  ]

  # Envoy ingress gateway alerts (5xx ratio, token fetch failures) when envoy is in gateways.enabled
  ingress_gateway_service_name = contains(local.env_vars.locals.gateways.enabled, "envoy") ? "envoy-gateway-${local.env_vars.locals.environment}" : ""

  # Central Agent Gateway
  enable_agent_gateway = true
  subnet_self_link     = dependency.networking.outputs.psc_subnet_id
  net_host_project_id  = dependency.projects.outputs.net_host_project_id
  vpc_name             = element(split("/", dependency.networking.outputs.network_id), 4)
  internal_root_ca_pem = dependency.security.outputs.internal_ca_cert_pem
  agent_invoker_sa_emails = [
    dependency.security.outputs.cx_mortgage_orchestrator_sa_email,
    dependency.security.outputs.ai_coe_mortgage_specialist_sa_email,
    dependency.security.outputs.test_vm_sa_email
  ]
  agent_project_ids = [
    dependency.projects.outputs.ai_coe_agents_project_id,
    dependency.projects.outputs.cx_agents_project_id
  ]
}
