# infrastructure/live/dev/stage-5-workloads/services/iap-egress/terragrunt.hcl
# Layer 5 (final step): grant roles/iap.egressor on every Agent Registry entry once the MCP
# services and the A2A agent have registered themselves. Keeps deploy-all single-pass.
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//5-workloads/services/iap-egress"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "governance" {
  config_path = "../../../stage-4-governance"
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    registry_service_id = "mock-registry-service"
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
}
