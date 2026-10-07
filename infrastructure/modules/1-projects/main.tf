# infrastructure/modules/1-projects/main.tf

resource "random_id" "project_suffix" {
  byte_length = 2
}

locals {
  suffix = random_id.project_suffix.hex

  # Resolve Project IDs dynamically: Use existing ID if BYO, otherwise generate unique name
  net_host_id   = var.byo_net_host_project ? var.existing_net_host_project : "${var.project_prefix}-net-host-${local.suffix}"
  gateway_id    = var.byo_gateway_project ? var.existing_gateway_project : "${var.project_prefix}-gateway-${local.suffix}"
  governance_id = var.byo_governance_project ? var.existing_governance_project : "${var.project_prefix}-governance-${local.suffix}"
  mcps_id       = "${var.project_prefix}-mcps-${local.suffix}"
  ai_coe_agents_id        = "${var.project_prefix}-ai-coe-agents-${local.suffix}"
  cx_agents_id = "${var.project_prefix}-cx-agents-${local.suffix}"

  # Systematic project-specific labeling mapping for FinOps and Cost Center attribution
  common_labels = {
    "env"        = var.environment
    "managed-by" = "terragrunt-esmeralda"
  }

  net_host_apis = [
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "dns.googleapis.com",
    "servicenetworking.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "certificatemanager.googleapis.com",
    "logging.googleapis.com"
  ]

  gateway_apis = [
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "apigee.googleapis.com",
    "certificatemanager.googleapis.com",
    "logging.googleapis.com",
    "secretmanager.googleapis.com",
    "run.googleapis.com",
    "iam.googleapis.com"
  ]

  mcps_apis = [
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "cloudbuild.googleapis.com",
    "agentregistry.googleapis.com"
  ]


  ai_coe_agents_apis = [
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

  cx_agents_apis = [
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
    "secretmanager.googleapis.com",
    "dlp.googleapis.com",
    "pubsub.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "looker.googleapis.com",
    "modelarmor.googleapis.com",
    "networkservices.googleapis.com",
    "agentregistry.googleapis.com",
    "iap.googleapis.com",
    "compute.googleapis.com",
    "networksecurity.googleapis.com",
    "aiplatform.googleapis.com"
  ]
}

# ====================================================================
# 1. GCP PROJECTS CREATION
# ====================================================================

# Provisioned conditionally: Only created if the customer does not BYO
resource "google_project" "net_host" {
  count               = var.byo_net_host_project ? 0 : 1
  name                = local.net_host_id
  project_id          = local.net_host_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = true
  deletion_policy     = "DELETE"

  labels = merge(local.common_labels, {
    "cost-center" = "networking-infrastructure"
    "team"        = "netops"
  })
}

# Provisioned conditionally: Only created if the customer does not BYO
resource "google_project" "gateway" {
  count               = var.byo_gateway_project ? 0 : 1
  name                = local.gateway_id
  project_id          = local.gateway_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = true
  deletion_policy     = "DELETE"

  labels = merge(local.common_labels, {
    "cost-center" = "ingress-gateways"
    "team"        = "platformops"
  })
}

# Central Tools Project: ALWAYS created by Esmeralda from scratch
resource "google_project" "mcps" {
  name                = local.mcps_id
  project_id          = local.mcps_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = true
  deletion_policy     = "DELETE"

  labels = merge(local.common_labels, {
    "cost-center" = "central-developer-tools"
    "team"        = "appdev-tools"
  })
}

# AI CoE agents project (reusable A2A agents owned by the AI CoE team): ALWAYS created from scratch
resource "google_project" "ai_coe_agents" {
  name                = local.ai_coe_agents_id
  project_id          = local.ai_coe_agents_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = true
  deletion_policy     = "DELETE"

  labels = merge(local.common_labels, {
    "cost-center"    = "enterprise-ai-platform"
    "team"           = "ai-coe"
    "agent_platform" = "agent-spoke-project"
  })
}

# CX agents project (user-facing orchestrator agents owned by the CX team): ALWAYS created from scratch
resource "google_project" "cx_agents" {
  name                = local.cx_agents_id
  project_id          = local.cx_agents_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = true
  deletion_policy     = "DELETE"

  labels = merge(local.common_labels, {
    "cost-center"    = "lob-business-solutions"
    "team"           = "cx"
    "agent_platform" = "agent-spoke-project"
  })
}

# Governance and Telemetry Hub Project: Conditional creation
resource "google_project" "governance" {
  count               = var.byo_governance_project ? 0 : 1
  name                = local.governance_id
  project_id          = local.governance_id
  folder_id           = var.folder_id != "" ? var.folder_id : null
  org_id              = var.folder_id == "" && var.org_id != "" ? var.org_id : null
  billing_account     = var.billing_account
  auto_create_network = true
  deletion_policy     = "DELETE"

  labels = merge(local.common_labels, {
    "cost-center" = "central-governance-and-telemetry"
    "team"        = "security-and-platformops"
  })
}

# ====================================================================
# 2. GCP SERVICE APIS ENABLEMENT
# ====================================================================

resource "null_resource" "serviceusage_bootstrap" {
  triggers = {
    net_host_id   = local.net_host_id
    mcps_id       = local.mcps_id
    ai_coe_agents_id        = local.ai_coe_agents_id
    cx_agents_id = local.cx_agents_id
    governance_id = local.governance_id
  }
  depends_on = [
    google_project.net_host,
    google_project.gateway,
    google_project.mcps,
    google_project.ai_coe_agents,
    google_project.cx_agents,
    google_project.governance
  ]

  provisioner "local-exec" {
    command = <<EOT
      echo "🚀 Activating serviceusage.googleapis.com directly via gcloud POST on newly created projects..."
      for p in ${local.net_host_id} ${local.gateway_id} ${local.mcps_id} ${local.ai_coe_agents_id} ${local.cx_agents_id} ${local.governance_id}; do
        if [ -n "$p" ] && [ "$p" != "null" ]; then
          echo "  -> Enabling serviceusage & cloudresourcemanager on $p..."
          gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com --project="$p" || true
        fi
      done
      sleep 15
    EOT
  }
}

# Enable APIs on Shared VPC project only if Esmeralda created it
resource "google_project_service" "net_host" {
  for_each                   = var.byo_net_host_project ? [] : toset(local.net_host_apis)
  project                    = local.net_host_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.net_host, null_resource.serviceusage_bootstrap]
}

# Enable APIs on Ingress Gateway project only if Esmeralda created it
resource "google_project_service" "gateway" {
  for_each                   = var.byo_gateway_project ? [] : toset(local.gateway_apis)
  project                    = local.gateway_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.gateway, null_resource.serviceusage_bootstrap]
}

# Enable Central Tools APIs
resource "google_project_service" "mcps" {
  for_each                   = toset(local.mcps_apis)
  project                    = local.mcps_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.mcps, null_resource.serviceusage_bootstrap]
}


# Enable Core AI Platform APIs
resource "google_project_service" "ai_coe_agents" {
  for_each                   = toset(local.ai_coe_agents_apis)
  project                    = local.ai_coe_agents_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.ai_coe_agents, null_resource.serviceusage_bootstrap]
}

# Enable Line-of-Business APIs
resource "google_project_service" "cx_agents" {
  for_each                   = toset(local.cx_agents_apis)
  project                    = local.cx_agents_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.cx_agents, null_resource.serviceusage_bootstrap]
}

# Enable Governance and Telemetry APIs only if created by Esmeralda
resource "google_project_service" "governance" {
  for_each                   = var.byo_governance_project ? [] : toset(local.governance_apis)
  project                    = local.governance_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.governance, null_resource.serviceusage_bootstrap]
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
    google_project_service.mcps,
    google_project_service.ai_coe_agents,
    google_project_service.cx_agents,
    google_project_service.governance
  ]
}

# Resolve pre-existing governance project details to obtain its project number for output if BYO is active
data "google_project" "governance" {
  count      = var.byo_governance_project ? 1 : 0
  project_id = var.existing_governance_project
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
resource "google_project_service_identity" "ai_coe_agents_run" {
  provider = google-beta
  project  = local.ai_coe_agents_id
  service  = "run.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Vertex AI Service Agent in Core AI platform project
resource "google_project_service_identity" "ai_coe_agents_vertex" {
  provider = google-beta
  project  = local.ai_coe_agents_id
  service  = "aiplatform.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Vertex AI Service Agent in the CX agents project
resource "google_project_service_identity" "cx_agents_vertex" {
  provider = google-beta
  project  = local.cx_agents_id
  service  = "aiplatform.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}

# Force provision Cloud Run Service Agent in CX agents project
resource "google_project_service_identity" "cx_agents_run" {
  provider = google-beta
  project  = local.cx_agents_id
  service  = "run.googleapis.com"

  depends_on = [time_sleep.api_propagation]
}


# Force provision Cloud SQL Service Agent in Core AI platform project
resource "google_project_service_identity" "ai_coe_agents_sql" {
  provider = google-beta
  project  = local.ai_coe_agents_id
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

# The CI/CD project and its Cloud Build service agent moved to the shared layer 0
# (live/shared/stage-0-cicd). Forget the old identity without calling the API.
removed {
  from = google_project_service_identity.cicd_build
  lifecycle {
    destroy = false
  }
}
