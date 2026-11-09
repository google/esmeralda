# infrastructure/modules/3-security/agent_identity.tf
# ==============================================================================
# ESMERALDA AGENT IDENTITY SECURITY & IAM PARITY CONFIGURATION
# ==============================================================================

locals {
  cx_agents_trust_domain     = "agents.global.${data.google_project.cx_agents.org_id != "" ? "org-${data.google_project.cx_agents.org_id}" : "proj-${data.google_project.cx_agents.number}"}.system.id.goog"
  ai_coe_agents_trust_domain = "agents.global.${data.google_project.ai_coe_agents.org_id != "" ? "org-${data.google_project.ai_coe_agents.org_id}" : "proj-${data.google_project.ai_coe_agents.number}"}.system.id.goog"

  # CX mortgage orchestrator identity PrincipalSet URI format for Vertex AI Reasoning Engine
  cx_mortgage_orchestrator_identity_principal = "principalSet://${local.cx_agents_trust_domain}/attribute.platformContainer/aiplatform/projects/${data.google_project.cx_agents.number}"

  # AI CoE mortgage specialist identity PrincipalSet URI format for Vertex AI Reasoning Engine
  ai_coe_mortgage_specialist_identity_principal = "principalSet://${local.ai_coe_agents_trust_domain}/attribute.platformContainer/aiplatform/projects/${data.google_project.ai_coe_agents.number}"
}

# 1. Shared Invoker Service Account for MCP Microservices
resource "google_service_account" "mcp_invoker_sa" {
  account_id   = "sa-mcp-invoker-${var.environment}"
  display_name = "Esmeralda MCP Microservices Invoker Service Account"
  project      = var.mcps_project_id
}

# 2. Grant Invoker SA Cloud Run Invoker role on the MCPs Project
resource "google_project_iam_member" "mcp_invoker_run_role" {
  project = var.mcps_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.mcp_invoker_sa.email}"
}

# 2b. Grant Cloud Run Invoker role on the MCPs Project directly to the AI CoE Specialist Agent Identity
# (CX orchestrator is deliberately excluded: it delegates tool work to the AI CoE specialist over A2A)
resource "google_project_iam_member" "ai_coe_mortgage_specialist_identity_mcp_run_invoker" {
  project = var.mcps_project_id
  role    = "roles/run.invoker"
  member  = local.ai_coe_mortgage_specialist_identity_principal
}

# 3a. Allow CX mortgage orchestrator identity PrincipalSet to Impersonate the Invoker SA
resource "google_service_account_iam_member" "cx_mortgage_orchestrator_identity_impersonates_mcp_invoker" {
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.cx_mortgage_orchestrator_identity_principal
}

# 3b. Allow A2A Agent Identity PrincipalSet to Impersonate the Invoker SA
resource "google_service_account_iam_member" "ai_coe_mortgage_specialist_identity_impersonates_mcp_invoker" {
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.ai_coe_mortgage_specialist_identity_principal
}

# 4a. Grant Full Role Parity to CX mortgage orchestrator PrincipalSet in the CX agents project
# (roles/aiplatform.user and roles/bigquery.dataEditor are managed per-agent by Layer 5 adk-agent)
resource "google_project_iam_member" "cx_mortgage_orchestrator_identity_roles" {
  for_each = toset([
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/telemetry.writer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/iam.serviceAccountTokenCreator",
    "roles/bigquery.jobUser"
  ])
  project = var.cx_agents_project_id
  role    = each.key
  member  = local.cx_mortgage_orchestrator_identity_principal
}

# 4b. Grant Full Role Parity to AI CoE mortgage specialist PrincipalSet in the AI CoE agents project
# (roles/aiplatform.user and roles/bigquery.dataEditor are managed per-agent by Layer 5 a2a-agent)
resource "google_project_iam_member" "ai_coe_mortgage_specialist_identity_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/telemetry.writer",
    "roles/storage.objectAdmin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/iam.serviceAccountTokenCreator",
    "roles/bigquery.jobUser"
  ])
  project = var.ai_coe_agents_project_id
  role    = each.key
  member  = local.ai_coe_mortgage_specialist_identity_principal
}

# Forget Layer 5-owned duplicate IAM bindings from Layer 3 state without deleting the live GCP bindings:
# - ai_coe_mortgage_specialist_identity_roles["roles/aiplatform.user"] -> owned by L5 a2a-agent (google_project_iam_member.agent_identity_aiplatform_user[0])
# - cx_mortgage_orchestrator_identity_roles["roles/aiplatform.user"]   -> owned by L5 adk-agent (google_project_iam_member.agent_identity_aiplatform_user[0])
# - ai_coe_mortgage_specialist_identity_roles["roles/bigquery.dataEditor"] -> owned by L5 a2a-agent (agent_identity_bq_writer)
# - cx_mortgage_orchestrator_identity_roles["roles/bigquery.dataEditor"]   -> owned by L5 adk-agent (agent_identity_bq_writer)
moved {
  from = google_project_iam_member.ai_coe_mortgage_specialist_identity_roles["roles/aiplatform.user"]
  to   = google_project_iam_member.l5_owned_ai_coe_specialist_id_aiplatform_user
}

removed {
  from = google_project_iam_member.l5_owned_ai_coe_specialist_id_aiplatform_user
  lifecycle {
    destroy = false
  }
}

moved {
  from = google_project_iam_member.cx_mortgage_orchestrator_identity_roles["roles/aiplatform.user"]
  to   = google_project_iam_member.l5_owned_cx_orchestrator_id_aiplatform_user
}

removed {
  from = google_project_iam_member.l5_owned_cx_orchestrator_id_aiplatform_user
  lifecycle {
    destroy = false
  }
}

moved {
  from = google_project_iam_member.ai_coe_mortgage_specialist_identity_roles["roles/bigquery.dataEditor"]
  to   = google_project_iam_member.l5_owned_ai_coe_specialist_id_bq_editor
}

removed {
  from = google_project_iam_member.l5_owned_ai_coe_specialist_id_bq_editor
  lifecycle {
    destroy = false
  }
}

moved {
  from = google_project_iam_member.cx_mortgage_orchestrator_identity_roles["roles/bigquery.dataEditor"]
  to   = google_project_iam_member.l5_owned_cx_orchestrator_id_bq_editor
}

removed {
  from = google_project_iam_member.l5_owned_cx_orchestrator_id_bq_editor
  lifecycle {
    destroy = false
  }
}

# 5a. Grant roles/iap.egressor to CX mortgage orchestrator identity ONLY on the AI CoE agents project
# (never on mcps_project_id: the orchestrator must call the A2A specialist, not MCP servers directly)
resource "google_project_iam_member" "cx_mortgage_orchestrator_iap_egressor" {
  for_each = toset([
    var.ai_coe_agents_project_id,
  ])
  project = each.key
  role    = "roles/iap.egressor"
  member  = local.cx_mortgage_orchestrator_identity_principal
}

# 5b. Grant roles/iap.egressor to A2A Agent Identity for Agent Gateway Egress Authz
resource "google_project_iam_member" "ai_coe_mortgage_specialist_iap_egressor" {
  project = var.mcps_project_id
  role    = "roles/iap.egressor"
  member  = local.ai_coe_mortgage_specialist_identity_principal
}
