# infrastructure/live/prd/layer-5-workloads/services/iap-egress/terragrunt.hcl
# Layer 5 (final step): grant least-privilege per-target roles/iap.egressor on each Agent Registry
# entry once the MCP services and Reasoning Engine agents have registered themselves.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "../../../../../modules//5-workloads/services/iap-egress"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "security" {
  config_path = "../../../layer-3-security"
}

dependency "governance" {
  config_path = "../../../layer-4-governance"
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-cx-mortgage-orchestrator"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    registry_service_id      = "mock-registry-service"
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/mock-ai-coe-mortgage-specialist"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "corporate_email" {
  config_path = "../corporate-email"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "income_verification" {
  config_path = "../income-verification"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "legacy_dms" {
  config_path = "../legacy-dms"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))

  # Callers allowed to invoke the A2A specialist through the Agent Gateway (never the specialist itself)
  specialist_agent_callers = [
    dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal,
    dependency.security.outputs.cx_mortgage_orchestrator_sa_email,
    dependency.security.outputs.test_vm_sa_email,
  ]

  # Callers allowed to invoke the MCP tool servers through the Agent Gateway (never cx-mortgage-orchestrator)
  mcp_server_callers = [
    dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal,
    dependency.security.outputs.ai_coe_mortgage_specialist_sa_email,
    dependency.security.outputs.mcp_invoker_sa_email,
    dependency.security.outputs.test_vm_sa_email,
  ]
}

inputs = {
  governance_project_id = dependency.projects.outputs.governance_project_id
  region                = local.env_vars.locals.region
  iap_egress_members    = dependency.governance.outputs.iap_egress_members
  registry_service_ids = [
    dependency.ai_coe_mortgage_specialist.outputs.registry_service_id,
    dependency.corporate_email.outputs.registry_service_id,
    dependency.income_verification.outputs.registry_service_id,
    dependency.legacy_dms.outputs.registry_service_id,
  ]
  target_iap_members = {
    agents = {
      "ai-coe-mortgage-specialist" = local.specialist_agent_callers
    }
    mcp_servers = {
      "corporate-email"     = local.mcp_server_callers
      "income-verification" = local.mcp_server_callers
      "legacy-dms"          = local.mcp_server_callers
    }
  }
}
