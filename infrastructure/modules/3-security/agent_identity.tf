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

# 0. Vertex AI Service Identities for Agent Identity projects
resource "google_project_service_identity" "aiplatform_agent_identity_a2a" {
  provider = google-beta
  project  = var.ai_coe_agents_project_id
  service  = "aiplatform.googleapis.com"
}

resource "google_project_service_identity" "aiplatform_agent_identity_adk" {
  provider = google-beta
  project  = var.cx_agents_project_id
  service  = "aiplatform.googleapis.com"
}

# 1. Shared Invoker Service Account (deprecated). Agents call out with their own Agent Identity: they
# mint unbound ID tokens for Cloud Run MCP hosts and access tokens for Agent Runtime hosts, and each
# callee grants the calling agent's principal:// from the service catalog. The account stays one
# release as the legacy_shared_invoker fallback; its grants (2, 3a, 3b) exist only while that is on.
resource "google_service_account" "mcp_invoker_sa" {
  account_id   = "sa-mcp-invoker-${var.environment}"
  display_name = "Esmeralda MCP Microservices Invoker Service Account (deprecated)"
  project      = var.mcps_project_id
}

# 2. Legacy fallback only: Cloud Run Invoker on the MCPs project for the shared invoker account.
resource "google_project_iam_member" "mcp_invoker_run_role" {
  count   = var.legacy_shared_invoker ? 1 : 0
  project = var.mcps_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.mcp_invoker_sa.email}"
}

moved {
  from = google_project_iam_member.mcp_invoker_run_role
  to   = google_project_iam_member.mcp_invoker_run_role[0]
}

# 2b. No agent identity holds project-level roles/run.invoker on the MCPs project (it used to be granted
# to the AI CoE agents principalSet, so any engine in that project could invoke every MCP service).
# Each MCP service grants run.invoker to the Agent Identity principal of each catalog agent caller.

# 3a/3b. Legacy fallback only: the agent principalSets may impersonate the shared invoker account.
# These SA-scoped grants are the only token-minting rights agent identities can hold: never a
# project-level roles/iam.serviceAccountTokenCreator, which would let them impersonate every service
# account of their project (in the CX agents project that includes the test VM SA, which can invoke
# the MCP services).
resource "google_service_account_iam_member" "cx_mortgage_orchestrator_identity_impersonates_mcp_invoker" {
  count              = var.legacy_shared_invoker ? 1 : 0
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.cx_mortgage_orchestrator_identity_principal
}

moved {
  from = google_service_account_iam_member.cx_mortgage_orchestrator_identity_impersonates_mcp_invoker
  to   = google_service_account_iam_member.cx_mortgage_orchestrator_identity_impersonates_mcp_invoker[0]
}

resource "google_service_account_iam_member" "ai_coe_mortgage_specialist_identity_impersonates_mcp_invoker" {
  count              = var.legacy_shared_invoker ? 1 : 0
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.ai_coe_mortgage_specialist_identity_principal
}

moved {
  from = google_service_account_iam_member.ai_coe_mortgage_specialist_identity_impersonates_mcp_invoker
  to   = google_service_account_iam_member.ai_coe_mortgage_specialist_identity_impersonates_mcp_invoker[0]
}

# 4a. Baseline observability & runtime roles for CX mortgage orchestrator PrincipalSet in the CX agents project
# (roles/aiplatform.user and roles/bigquery.dataEditor are bound in Layer 5 adk-agent to avoid duplicate bindings)
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
    "roles/bigquery.jobUser"
  ])
  project = var.cx_agents_project_id
  role    = each.key
  member  = local.cx_mortgage_orchestrator_identity_principal
}

# 4b. Baseline observability, DLP & runtime roles for AI CoE mortgage specialist PrincipalSet in the AI CoE agents project
# (roles/aiplatform.user and roles/bigquery.dataEditor are bound in Layer 5 a2a-agent to avoid duplicate bindings)
resource "google_project_iam_member" "ai_coe_mortgage_specialist_identity_roles" {
  for_each = toset([
    "roles/dlp.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/telemetry.writer",
    "roles/storage.objectAdmin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/bigquery.jobUser"
  ])
  project = var.ai_coe_agents_project_id
  role    = each.key
  member  = local.ai_coe_mortgage_specialist_identity_principal
}

# 5. Per-target IAP egress policies (agentregistry-...) are bound per SPIFFE principal:// in Layer 5 iap-egress.
resource "google_project_iam_member" "cx_mortgage_orchestrator_iap_egressor" {
  # Project-wide roles/iap.egressor is disabled here (scoped only to var.ai_coe_agents_project_id when enabled).
  for_each = toset([])
  project  = each.key
  role     = "roles/iap.egressor"
  member   = local.cx_mortgage_orchestrator_identity_principal
}

# Forget duplicate bindings now owned by Layer 5 (adk-agent / a2a-agent) without deleting them in GCP IAM
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
  from = google_project_iam_member.cx_mortgage_orchestrator_identity_roles["roles/bigquery.dataEditor"]
  to   = google_project_iam_member.l5_owned_cx_orchestrator_id_bq_editor
}

removed {
  from = google_project_iam_member.l5_owned_cx_orchestrator_id_bq_editor
  lifecycle {
    destroy = false
  }
}

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
  from = google_project_iam_member.ai_coe_mortgage_specialist_identity_roles["roles/bigquery.dataEditor"]
  to   = google_project_iam_member.l5_owned_ai_coe_specialist_id_bq_editor
}

removed {
  from = google_project_iam_member.l5_owned_ai_coe_specialist_id_bq_editor
  lifecycle {
    destroy = false
  }
}
