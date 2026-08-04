# infrastructure/modules/3-security/agent_identity.tf
# ====================================================================
# VERTEX AI AGENT IDENTITY & MCP INVOKER SERVICE ACCOUNT BINDINGS
# ====================================================================

locals {
  root_agent_identity_principal = (
    var.org_id != "" && var.org_id != null
    ? "principalSet://agents.global.org-${var.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.root_agent.number}"
    : "principalSet://agents.global.project-${data.google_project.root_agent.number}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.root_agent.number}"
  )

  a2a_agent_identity_principal = (
    var.org_id != "" && var.org_id != null
    ? "principalSet://agents.global.org-${var.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.a2a.number}"
    : "principalSet://agents.global.project-${data.google_project.a2a.number}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.a2a.number}"
  )
}

# 1. Dedicated Invoker Service Account for Cloud Run MCP Microservices
resource "google_service_account" "mcp_invoker_sa" {
  account_id   = "sa-mcp-invoker-${var.environment}"
  display_name = "MCP Invoker Service Account"
  project      = var.mcps_project_id
}

# 2. Grant Invoker SA Cloud Run Invoker Role on MCP Services Project
resource "google_project_iam_member" "mcp_invoker_run_invoker" {
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
    "roles/iam.serviceAccountTokenCreator"
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
    "roles/iam.serviceAccountTokenCreator"
  ])
  project = var.a2a_project_id
  role    = each.key
  member  = local.a2a_agent_identity_principal
}
