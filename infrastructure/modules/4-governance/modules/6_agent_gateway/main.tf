# infrastructure/modules/4-governance/modules/6_agent_gateway/main.tf
# ==============================================================================
# CENTRALIZED AGENT GATEWAY & AGENT REGISTRY MODULE (August 2026 Release)
# ==============================================================================

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    google-beta = {
      source = "hashicorp/google-beta"
      # >= 8.4.0 required for google_network_services_agent_connectivity_template
      version = ">= 8.4.0, < 9.0.0"
    }
  }
}

data "google_project" "governance" {
  project_id = var.governance_project_id
}

# 0. Enable Vertex AI Platform & Certificate Manager APIs in Governance Project
resource "google_project_service" "aiplatform" {
  count                      = var.enable_agent_gateway ? 1 : 0
  project                    = var.governance_project_id
  service                    = "aiplatform.googleapis.com"
  disable_on_destroy         = false
  disable_dependent_services = false
}

resource "google_project_service" "certificatemanager" {
  count                      = var.enable_agent_gateway ? 1 : 0
  project                    = var.governance_project_id
  service                    = "certificatemanager.googleapis.com"
  disable_on_destroy         = false
  disable_dependent_services = false
}

# 1. Dedicated PSC Network Attachment in Governance Project referencing Shared VPC Subnet
resource "google_compute_network_attachment" "agent_gateway" {
  count                 = var.enable_agent_gateway && var.subnet_self_link != "" ? 1 : 0
  project               = var.governance_project_id
  name                  = "agw-egress-na-${var.environment}"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.subnet_self_link]
}

# Grant roles/dns.peer to Agent Gateway Service Agent on Shared VPC Host Project (Official GCP Requirement)
resource "google_project_iam_member" "agent_gateway_dns_peer" {
  count   = var.enable_agent_gateway && var.net_host_project_id != "" ? 1 : 0
  project = var.net_host_project_id
  role    = "roles/dns.peer"
  member  = "serviceAccount:service-${data.google_project.governance.number}@gcp-sa-agentgateway.iam.gserviceaccount.com"
}

# Grant roles/compute.networkUser to Agent Gateway Service Agent on Shared VPC Host Project
# (required when the network attachment references a Shared VPC subnet)
resource "google_project_iam_member" "agent_gateway_network_user" {
  count   = var.enable_agent_gateway && var.net_host_project_id != "" ? 1 : 0
  project = var.net_host_project_id
  role    = "roles/compute.networkUser"
  member  = "serviceAccount:service-${data.google_project.governance.number}@gcp-sa-agentgateway.iam.gserviceaccount.com"
}

# 2a. Agent Connectivity Template (egress networking + private CA trust)
# - Private ranges (esmeralda.internal -> Kong ILB) egress through the PSC network attachment.
# - Public Google APIs use Private Google Access automatically (never peer googleapis.com).
# - TLS to *.esmeralda.internal is validated against the internal Root CA TrustConfig.
# NOTE: ACTs are immutable while referenced by a gateway; changing one forces a gateway replace.
resource "google_network_services_agent_connectivity_template" "egress" {
  count                          = var.enable_agent_gateway && var.subnet_self_link != "" ? 1 : 0
  provider                       = google-beta
  project                        = var.governance_project_id
  location                       = var.region
  agent_connectivity_template_id = "esmeralda-egress-act-${var.environment}"
  description                    = "Esmeralda AGW egress: PSC-I to Shared VPC, esmeralda.internal DNS peering, internal Root CA trust"

  access_path = "AGENT_TO_ANYWHERE"

  egress_network_config {
    network_attachment = google_compute_network_attachment.agent_gateway[0].id
    vpc_egress         = "PRIVATE_RANGES_ONLY"

    dns_peering_config {
      domains        = ["esmeralda.internal."]
      target_network = startswith(var.vpc_name, "projects/") ? var.vpc_name : "projects/${var.net_host_project_id}/global/networks/${var.vpc_name}"
    }

    dynamic "tls_config" {
      for_each = length(google_certificate_manager_trust_config.internal_trust_config) > 0 ? [1] : []
      content {
        trust_config     = "projects/${data.google_project.governance.number}/locations/${var.region}/trustConfigs/${google_certificate_manager_trust_config.internal_trust_config[0].name}"
        additional_roots = "PUBLICLY_TRUSTED_ROOTS"
      }
    }
  }

  depends_on = [
    google_project_iam_member.agent_gateway_dns_peer,
    google_project_iam_member.agent_gateway_network_user,
  ]
}

# 2b. Central Agent Gateway Resource (AGENT_TO_ANYWHERE Egress Mode)
# The ACT must be set at creation time; gateways created without one cannot be migrated.
resource "google_network_services_agent_gateway" "egress_gateway" {
  count    = var.enable_agent_gateway && var.subnet_self_link != "" ? 1 : 0
  provider = google-beta
  project  = var.governance_project_id
  location = var.region
  name     = "esmeralda-agent-egress-gateway-${var.environment}"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  agent_connectivity_template = "projects/${data.google_project.governance.number}/locations/${var.region}/agentConnectivityTemplates/${google_network_services_agent_connectivity_template.egress[0].agent_connectivity_template_id}"

  registries = [
    "//agentregistry.googleapis.com/projects/${data.google_project.governance.number}/locations/${var.region}"
  ]

  lifecycle {
    # Swapping the ACT on a live gateway is unsupported: recreate the gateway instead.
    # (Agent Runtimes bound to it must be detached/deleted before the replace.)
    replace_triggered_by = [google_network_services_agent_connectivity_template.egress]
  }
}

# 3. IAP Authorization Extension (REQUEST_AUTHZ)
resource "google_network_services_authz_extension" "iap_authz" {
  count     = var.enable_agent_gateway && var.subnet_self_link != "" ? 1 : 0
  provider  = google-beta
  project   = var.governance_project_id
  location  = var.region
  name      = "agw-iap-authz-${var.environment}"
  service   = "iap.googleapis.com"
  timeout   = "3s"
  fail_open = false

  metadata = {
    iapPolicyVersion = "V1"
  }
}

# 4. IAP Network Security Authorization Policy (REQUEST_AUTHZ)
resource "google_network_security_authz_policy" "iap_policy" {
  count          = var.enable_agent_gateway && var.subnet_self_link != "" ? 1 : 0
  provider       = google-beta
  project        = var.governance_project_id
  location       = var.region
  name           = "agw-iap-policy-${var.environment}"
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  target {
    resources = [google_network_services_agent_gateway.egress_gateway[0].id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.iap_authz[0].id]
    }
  }
}

# 5. Model Armor Authorization Extension (CONTENT_AUTHZ) - Optional
resource "google_network_services_authz_extension" "model_armor_content_authz" {
  count     = 0
  provider  = google-beta
  project   = var.governance_project_id
  location  = var.region
  name      = "model-armor-authz-ext-${var.environment}"
  service   = "modelarmor.${var.region}.rep.googleapis.com"
  fail_open = true
  timeout   = "2s"

  metadata = {
    templateName = var.model_armor_template_name
  }
}

# 4. Model Armor Network Security Authorization Policy (CONTENT_AUTHZ)
resource "google_network_security_authz_policy" "model_armor_policy" {
  count    = 0
  provider = google-beta
  project  = var.governance_project_id
  location = var.region
  name     = "model-armor-authz-policy-${var.environment}"

  target {
    resources = [google_network_services_agent_gateway.egress_gateway[0].id]
  }

  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor_content_authz[0].id]
    }
  }
}

resource "google_project_iam_member" "networksecurity_model_armor" {
  count   = var.enable_agent_gateway ? 1 : 0
  project = var.governance_project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:service-${data.google_project.governance.number}@gcp-sa-networksecurity.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "agentgateway_model_armor" {
  count   = var.enable_agent_gateway ? 1 : 0
  project = var.governance_project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:service-${data.google_project.governance.number}@gcp-sa-agentgateway.iam.gserviceaccount.com"
}

# 5. Declarative System Google API Endpoints registered in Central Agent Registry
# IAP egress requires every exact hostname the agents call through the gateway to be registered.
locals {
  google_apis = {
    aiplatform           = "Vertex AI Platform"
    modelarmor           = "Model Armor"
    cloudresourcemanager = "Cloud Resource Manager"
    logging              = "Logging"
    monitoring           = "Monitoring"
    telemetry            = "Telemetry"
    cloudtrace           = "Cloud Trace"
    secretmanager        = "Secret Manager"
    agentregistry        = "Agent Registry"
    iap                  = "Identity-Aware Proxy"
    iamcredentials       = "IAM Credentials"
    bigquery             = "BigQuery"
    bigquerystorage      = "BigQuery Storage"
  }

  system_endpoints = var.enable_agent_gateway ? merge([
    for id, name in local.google_apis : {
      (length(id) >= 4 ? id : "${id}-endpoint") = {
        display_name = name
        url          = "https://${id}.googleapis.com"
      }
      "${id}-mtls" = {
        display_name = "${name} mTLS"
        url          = "https://${id}.mtls.googleapis.com"
      }
      "${var.region}-${id}" = {
        display_name = "${name} Regional"
        url          = "https://${var.region}-${id}.googleapis.com"
      }
      "${var.region}-${id}-mtls" = {
        display_name = "${name} Regional mTLS"
        url          = "https://${var.region}-${id}.mtls.googleapis.com"
      }
      "${id}-${var.region}-rep" = {
        display_name = "${name} Regional REP"
        url          = "https://${id}.${var.region}.rep.googleapis.com"
      }
      "us-${id}" = {
        display_name = "${name} US Multi-Region"
        url          = "https://us-${id}.googleapis.com"
      }
      "us-${id}-mtls" = {
        display_name = "${name} US Multi-Region mTLS"
        url          = "https://us-${id}.mtls.googleapis.com"
      }
    }
  ]...) : {}
}

resource "google_agent_registry_service" "system_endpoints" {
  for_each     = local.system_endpoints
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = each.key
  display_name = each.value.display_name

  interfaces {
    url              = each.value.url
    protocol_binding = can(regex("bigquerystorage", each.key)) ? "GRPC" : "HTTP_JSON"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

# 6. Agent Gateway root CA bundle: the gateway's TLS-inspection roots + the internal Root CA
#    (from layer 3 security). Injected into agents at deploy time (AGENT_GATEWAY_ROOT_CERTIFICATES)
#    and published to Secret Manager for operators/test clients.
locals {
  agw_root_ca_bundle = length(google_network_services_agent_gateway.egress_gateway) > 0 ? join("\n", compact(concat(
    [for c in google_network_services_agent_gateway.egress_gateway[0].agent_gateway_card[0].root_certificates : trimspace(c)],
    [trimspace(var.internal_root_ca_pem)]
  ))) : ""
}

resource "google_secret_manager_secret" "agw_ca_cert" {
  count     = var.enable_agent_gateway ? 1 : 0
  project   = var.governance_project_id
  secret_id = "agw-root-ca-cert-${var.environment}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "agw_ca_cert_latest" {
  count       = var.enable_agent_gateway && length(google_network_services_agent_gateway.egress_gateway) > 0 ? 1 : 0
  secret      = google_secret_manager_secret.agw_ca_cert[0].id
  secret_data = local.agw_root_ca_bundle
}

# 7. Grant Secret Accessor to Agent Service Accounts and Principals
resource "google_secret_manager_secret_iam_member" "agw_ca_secret_accessors" {
  for_each  = var.enable_agent_gateway ? toset(var.agent_invoker_sa_emails) : toset([])
  project   = var.governance_project_id
  secret_id = google_secret_manager_secret.agw_ca_cert[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = startswith(each.value, "serviceAccount:") || startswith(each.value, "principalSet:") ? each.value : "serviceAccount:${each.value}"
}

data "google_project" "agent_projects" {
  for_each   = toset(var.agent_project_ids)
  project_id = each.value
}

locals {
  dynamic_agent_members = flatten([
    for proj_id, p in data.google_project.agent_projects : [
      "serviceAccount:service-${p.number}@gcp-sa-aiplatform.iam.gserviceaccount.com",
      "serviceAccount:service-${p.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
      p.org_id != "" ? "principalSet://agents.global.org-${p.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${p.number}" : "",
      p.org_id != "" ? "principalSet://agents.global.org-${p.org_id}.system.id.goog/attribute.container/projects/${p.number}" : "",
      p.org_id != "" ? "principalSet://agents.global.org-${p.org_id}.system.id.goog/*" : ""
    ]
  ])

  all_iap_members = distinct(concat(
    var.agent_invoker_sa_emails,
    local.dynamic_agent_members
  ))

  cleaned_iap_members = [for m in local.all_iap_members : m if m != ""]

  iap_egress_members = [
    for m in local.cleaned_iap_members :
    startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"
  ]
}

# 8. Grant roles/iap.egressor registry-wide (+ any entries already registered). Layer 5 re-runs the
#    same script after the MCP services and agents register themselves (stack services/iap-egress).
resource "null_resource" "grant_iap_egress" {
  count      = var.enable_agent_gateway ? 1 : 0
  depends_on = [google_agent_registry_service.system_endpoints]

  triggers = {
    services_hash = md5(jsonencode(local.system_endpoints))
    members_hash  = md5(jsonencode(local.iap_egress_members))
    gateway_id    = try(google_network_services_agent_gateway.egress_gateway[0].id, "")
    version       = "1"
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      GOVERNANCE_PROJECT_ID = var.governance_project_id
      REGION                = var.region
      IAP_MEMBERS_JSON      = jsonencode(local.iap_egress_members)
    }
  }
}

# 9. Certificate Manager TrustConfig for Internal Root CA (*.esmeralda.internal)
resource "google_certificate_manager_trust_config" "internal_trust_config" {
  count       = var.enable_agent_gateway && var.internal_root_ca_pem != "" ? 1 : 0
  project     = var.governance_project_id
  location    = var.region
  name        = "esmeralda-internal-trust-${var.environment}"
  description = "TrustConfig for *.esmeralda.internal private endpoints"

  trust_stores {
    trust_anchors {
      pem_certificate = var.internal_root_ca_pem
    }
  }

  depends_on = [google_project_service.certificatemanager]
}
