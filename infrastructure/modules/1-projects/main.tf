# infrastructure/modules/1-projects/main.tf

resource "random_id" "project_suffix" {
  byte_length = 2
}

locals {
  suffix = random_id.project_suffix.hex
  
  # Resolve Project IDs dynamically: Use existing ID if BYO, otherwise generate unique name
  net_host_id   = var.byo_net_host_project ? var.existing_net_host_project : "${var.project_prefix}-net-host-${local.suffix}"
  gateway_id    = var.byo_gateway_project  ? var.existing_gateway_project  : "${var.project_prefix}-gateway-${local.suffix}"
  governance_id = var.byo_governance_project ? var.existing_governance_project : "${var.project_prefix}-governance-${local.suffix}"
  cicd_id       = var.byo_cicd_project     ? var.existing_cicd_project     : "${var.project_prefix}-cicd-artifacts-${local.suffix}"
  mcps_id       = "${var.project_prefix}-mcps-${local.suffix}"
  a2a_id        = "${var.project_prefix}-a2a-${local.suffix}"
  root_agent_id = "${var.project_prefix}-root-agent-${local.suffix}"

  # Systematic project-specific labeling mapping for FinOps and Cost Center attribution
  common_labels = {
    "env"        = var.environment
    "managed-by" = "terragrunt-esmeralda"
  }

  net_host_apis = [
    "compute.googleapis.com",
    "dns.googleapis.com",
    "servicenetworking.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "certificatemanager.googleapis.com",
    "logging.googleapis.com"
  ]

  gateway_apis = [
    "compute.googleapis.com",
    "apigee.googleapis.com",
    "certificatemanager.googleapis.com",
    "logging.googleapis.com",
    "secretmanager.googleapis.com",
    "run.googleapis.com",
    "iam.googleapis.com"
  ]

  cicd_apis = [
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com"
  ]

  mcps_apis = [
    "compute.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "cloudbuild.googleapis.com"
  ]


  a2a_apis = [
    "compute.googleapis.com",
    "aiplatform.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "servicenetworking.googleapis.com",
    "bigquerystorage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "telemetry.googleapis.com",
    "iamcredentials.googleapis.com",
    "agentregistry.googleapis.com",
    "apphub.googleapis.com",
    "apptopology.googleapis.com",
    "dataform.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "notebooks.googleapis.com",
    "observability.googleapis.com",
    "appengine.googleapis.com",
    "securitycenter.googleapis.com",
    "texttospeech.googleapis.com",
    "saasservicemgmt.googleapis.com",
    "cloudapiregistry.googleapis.com",
    "iamconnectors.googleapis.com"
  ]

  root_agent_apis = [
    "compute.googleapis.com",
    "aiplatform.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "servicenetworking.googleapis.com",
    "bigquerystorage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "telemetry.googleapis.com",
    "iamcredentials.googleapis.com",
    "agentregistry.googleapis.com",
    "apphub.googleapis.com",
    "apptopology.googleapis.com",
    "dataform.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "notebooks.googleapis.com",
    "observability.googleapis.com",
    "appengine.googleapis.com",
    "securitycenter.googleapis.com",
    "texttospeech.googleapis.com",
    "saasservicemgmt.googleapis.com",
    "cloudapiregistry.googleapis.com",
    "iamconnectors.googleapis.com"
  ]


  governance_apis = [
    "bigquery.googleapis.com",
    "logging.googleapis.com",
    "clouderrorreporting.googleapis.com",
    "cloudtrace.googleapis.com",
    "monitoring.googleapis.com",
    "cloudkms.googleapis.com",
    "secretmanager.googleapis.com"
  ]
}

# ====================================================================
# 1. GCP PROJECTS CREATION
# ====================================================================

# Provisioned conditionally: Only created if the customer does not BYO
resource "google_project" "net_host" {
  count           = var.byo_net_host_project ? 0 : 1
  name            = "Esmeralda Shared VPC Host"
  project_id      = local.net_host_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "networking-infrastructure"
    "team"        = "netops"
  })
}

# Provisioned conditionally: Only created if the customer does not BYO
resource "google_project" "gateway" {
  count           = var.byo_gateway_project ? 0 : 1
  name            = "Esmeralda Ingress Gateway"
  project_id      = local.gateway_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "ingress-gateways"
    "team"        = "platformops"
  })
}

# Central CI/CD & Artifacts Project: Conditional creation
resource "google_project" "cicd" {
  count           = var.byo_cicd_project ? 0 : 1
  name            = "Esmeralda CI-CD Artifacts"
  project_id      = local.cicd_id

  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "shared-cicd-and-artifacts"
    "team"        = "platform-engineering"
  })
}

# Central Tools Project: ALWAYS created by Esmeralda from scratch
resource "google_project" "mcps" {
  name            = "Esmeralda MCP Server Tools"
  project_id      = local.mcps_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "central-developer-tools"
    "team"        = "appdev-tools"
  })
}

# Core AI Platform Project: ALWAYS created by Esmeralda from scratch
resource "google_project" "a2a" {
  name            = "Esmeralda A2A Core Agents"
  project_id      = local.a2a_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "enterprise-ai-platform"
    "team"        = "core-ai-agents"
  })
}

# Line-of-Business User Facing Root Agent Project: ALWAYS created from scratch
resource "google_project" "root_agent" {
  name            = "Esmeralda LOB Root Agent"
  project_id      = local.root_agent_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "lob-business-solutions"
    "team"        = "lob-root-agent"
  })
}

# Governance and Telemetry Hub Project: Conditional creation
resource "google_project" "governance" {
  count           = var.byo_governance_project ? 0 : 1
  name            = "Esmeralda Governance Hub"
  project_id      = local.governance_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  org_id          = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "central-governance-and-telemetry"
    "team"        = "security-and-platformops"
  })
}

# ====================================================================
# 2. GCP SERVICE APIS ENABLEMENT
# ====================================================================

# Enable APIs on Shared VPC project only if Esmeralda created it
resource "google_project_service" "net_host" {
  for_each                   = var.byo_net_host_project ? [] : toset(local.net_host_apis)
  project                    = local.net_host_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.net_host]
}

# Enable APIs on Ingress Gateway project only if Esmeralda created it
resource "google_project_service" "gateway" {
  for_each                   = var.byo_gateway_project ? [] : toset(local.gateway_apis)
  project                    = local.gateway_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.gateway]
}

# Enable CI/CD & Artifacts APIs
resource "google_project_service" "cicd" {
  for_each                   = var.byo_cicd_project ? [] : toset(local.cicd_apis)
  project                    = local.cicd_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.cicd]
}

# Enable Central Tools APIs
resource "google_project_service" "mcps" {
  for_each                   = toset(local.mcps_apis)
  project                    = local.mcps_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.mcps]
}


# Enable Core AI Platform APIs
resource "google_project_service" "a2a" {
  for_each                   = toset(local.a2a_apis)
  project                    = local.a2a_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.a2a]
}

# Enable Line-of-Business APIs
resource "google_project_service" "root_agent" {
  for_each                   = toset(local.root_agent_apis)
  project                    = local.root_agent_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.root_agent]
}

# Enable Governance and Telemetry APIs only if created by Esmeralda
resource "google_project_service" "governance" {
  for_each                   = var.byo_governance_project ? [] : toset(local.governance_apis)
  project                    = local.governance_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.governance]
}

# ====================================================================
# 3. GOOGLE-MANAGED SERVICE AGENTS (BOOTSTRAPPING RUNTIME IDENTITIES)
# ====================================================================

# Delay to allow newly enabled APIs to fully propagate across GCP's eventually consistent metadata servers
resource "time_sleep" "api_propagation" {
  create_duration = "30s"

  depends_on = [
    google_project_service.net_host,
    google_project_service.gateway,
    google_project_service.cicd,
    google_project_service.mcps,
    google_project_service.a2a,
    google_project_service.root_agent,
    google_project_service.governance
  ]
}

# Resolve pre-existing governance project details to obtain its project number for output if BYO is active
data "google_project" "governance" {
  count      = var.byo_governance_project ? 1 : 0
  project_id = var.existing_governance_project
}

# Force provision Cloud Build Service Agent in CI/CD project
resource "google_project_service_identity" "cicd_build" {
  provider = google-beta
  project  = local.cicd_id
  service  = "cloudbuild.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Cloud Run Service Agent in MCP central tools project
resource "google_project_service_identity" "mcps_run" {
  provider = google-beta
  project  = local.mcps_id
  service  = "run.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}


# Force provision Cloud Run Service Agent in Gateway project
resource "google_project_service_identity" "gateway_run" {
  provider = google-beta
  project  = local.gateway_id
  service  = "run.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Cloud Run Service Agent in Core AI platform project
resource "google_project_service_identity" "a2a_run" {
  provider = google-beta
  project  = local.a2a_id
  service  = "run.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Vertex AI Service Agent in Core AI platform project
resource "google_project_service_identity" "a2a_vertex" {
  provider = google-beta
  project  = local.a2a_id
  service  = "aiplatform.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Vertex AI Service Agent in Root Agent project
resource "google_project_service_identity" "root_vertex" {
  provider = google-beta
  project  = local.root_agent_id
  service  = "aiplatform.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Cloud Run Service Agent in Root Agent project
resource "google_project_service_identity" "root_run" {
  provider = google-beta
  project  = local.root_agent_id
  service  = "run.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}


# Force provision Cloud SQL Service Agent in Core AI platform project
resource "google_project_service_identity" "a2a_sql" {
  provider = google-beta
  project  = local.a2a_id
  service  = "sqladmin.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Secret Manager Service Agent in Governance project (skipped if BYO to avoid Project IAM Admin requirements)
resource "google_project_service_identity" "governance_secrets" {
  provider = google-beta
  count    = var.byo_governance_project ? 0 : 1
  project  = local.governance_id
  service  = "secretmanager.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}


