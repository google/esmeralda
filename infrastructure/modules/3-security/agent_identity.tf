# infrastructure/modules/3-security/agent_identity.tf
# ==============================================================================
# ESMERALDA AGENT IDENTITY SECURITY & IAM PARITY CONFIGURATION
# ==============================================================================

locals {
  # Root Agent Identity PrincipalSet URI format for Vertex AI Reasoning Engine
  root_agent_identity_principal = "principalSet://agents.global.org-${data.google_project.root_agent.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.root_agent.number}"

  # Core A2A Agent Identity PrincipalSet URI format for Vertex AI Reasoning Engine
  a2a_agent_identity_principal = "principalSet://agents.global.org-${data.google_project.a2a.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.a2a.number}"
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

# 3a. Allow Root Agent Identity PrincipalSet to Impersonate the Invoker SA
resource "google_service_account_iam_member" "root_agent_identity_impersonates_mcp_invoker" {
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.root_agent_identity_principal
}

# 3b. Allow A2A Agent Identity PrincipalSet to Impersonate the Invoker SA
resource "google_service_account_iam_member" "a2a_agent_identity_impersonates_mcp_invoker" {
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.a2a_agent_identity_principal
}

# 4a. Grant Full Role Parity to Root Agent PrincipalSet in Root Project
resource "google_project_iam_member" "root_agent_identity_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/telemetry.writer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/iam.serviceAccountTokenCreator",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser"
  ])
  project = var.root_project_id
  role    = each.key
  member  = local.root_agent_identity_principal
}

# 4b. Grant Full Role Parity to A2A Agent PrincipalSet in A2A Project
resource "google_project_iam_member" "a2a_agent_identity_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/telemetry.writer",
    "roles/storage.objectAdmin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/iam.serviceAccountTokenCreator",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser"
  ])
  project = var.a2a_project_id
  role    = each.key
  member  = local.a2a_agent_identity_principal
}

# 5a. Grant roles/iap.egressor to Root Agent Identity for Agent Gateway Egress Authz
resource "google_project_iam_member" "root_agent_iap_egressor" {
  for_each = toset([
    var.mcps_project_id,
    var.a2a_project_id,
  ])
  project = each.key
  role    = "roles/iap.egressor"
  member  = local.root_agent_identity_principal
}

# 5b. Grant roles/iap.egressor to A2A Agent Identity for Agent Gateway Egress Authz
resource "google_project_iam_member" "a2a_agent_iap_egressor" {
  project = var.mcps_project_id
  role    = "roles/iap.egressor"
  member  = local.a2a_agent_identity_principal
}
