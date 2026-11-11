# infrastructure/live/dev/layer-5-workloads/iap-egress/terragrunt.hcl
# Layer 5 (final step): grant least-privilege per-target roles/iap.egressor on each Agent Registry
# entry once the MCP services and Reasoning Engine agents have registered themselves.
#
# Who may call each target comes from the service catalog: the callers in catalog/ compile into
# catalog/.resolved/<env>.json `iap_egress` (env.yaml catalog_iap_egress) as `<unit>:<output>`
# references; identity_refs below maps them to unit outputs. Adding a caller in catalog/ updates both
# the run.invoker / aiplatform.user grants and these gateway lists. A new agent caller also needs its
# dependency block and its identity_refs entry here (the plan fails on an unmapped reference).
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../modules//5-workloads/iap-egress"
}

dependency "projects" {
  config_path = "../../layer-1-projects"
}

dependency "security" {
  config_path = "../../layer-3-security"
}

dependency "governance" {
  config_path = "../../layer-4-governance"
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-cx-mortgage-orchestrator"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    registry_service_id      = "mock-registry-service"
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-ai-coe-mortgage-specialist"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "corporate_email" {
  config_path = "../services/corporate-email"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "income_verification" {
  config_path = "../services/income-verification"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "legacy_dms" {
  config_path = "../services/legacy-dms"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  governance_project_id = dependency.projects.outputs.governance_project_id
  region                = local.env_vars.locals.region
  # Same registry-root members as layer 4, so both writers produce the identical root policy
  # (conditioned to system endpoint entries by grant_iap_egress.sh).
  iap_egress_members = dependency.governance.outputs.iap_egress_members
  registry_service_ids = [
    dependency.ai_coe_mortgage_specialist.outputs.registry_service_id,
    dependency.corporate_email.outputs.registry_service_id,
    dependency.income_verification.outputs.registry_service_id,
    dependency.legacy_dms.outputs.registry_service_id,
  ]

  # Per-target callers, compiled from the catalog ({agents = {...}, mcp_servers = {...}} of refs):
  # never hand-written here. The orchestrator is a caller of the specialist only, never of an MCP server.
  target_iap_members = local.env_vars.locals.catalog_iap_egress

  # Values of the references the compiled lists use.
  identity_refs = merge(
    # layer-3-security:<name>_sa_email: the service accounts layer 3 creates.
    { for k, v in dependency.security.outputs : "layer-3-security:${k}" => v if can(regex("_sa_email$", k)) && v != null },
    # agents/<name>:agent_identity_principal: each agent's Agent Identity principal (principal://).
    {
      "agents/cx-mortgage-orchestrator:agent_identity_principal"   = dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal
      "agents/ai-coe-mortgage-specialist:agent_identity_principal" = dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal
    },
  )
}
