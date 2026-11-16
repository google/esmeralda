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
# - Private ranges (esmeralda.internal -> internal entry point ILB) egress through the PSC network attachment.
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

# 5. Model Armor Authorization Extension (CONTENT_AUTHZ), env.yaml governance.model_armor.gateway
# The callout sends the matched request/response payloads to the regional Model Armor endpoint
# with the prompt (request) and response templates of submodule 5_model_armor.
resource "google_network_services_authz_extension" "model_armor_content_authz" {
  count     = var.enable_agent_gateway && var.subnet_self_link != "" && var.model_armor_gateway ? 1 : 0
  provider  = google-beta
  project   = var.governance_project_id
  location  = var.region
  name      = "model-armor-authz-ext-${var.environment}"
  service   = "modelarmor.${var.region}.rep.googleapis.com"
  fail_open = var.model_armor_fail_open
  # Per message; Model Armor can take several seconds. With fail_open = true a timeout skips the
  # check silently, with fail_open = false it rejects the call.
  timeout = format("%gs", var.model_armor_timeout_ms / 1000)

  metadata = {
    model_armor_settings = jsonencode([{
      request_template_id  = var.model_armor_template_name
      response_template_id = var.model_armor_response_tpl
    }])
  }
}

# 6. Model Armor Network Security Authorization Policy (CONTENT_AUTHZ)
# Scoped to unary payloads Model Armor inspects: MCP tools/call and prompts/get (our MCP servers
# answer with JSON, not SSE) and A2A agent cards. Every other request (A2A messages, which can run
# for minutes, Gemini calls, streams) never matches, so it never opens a Model Armor callout.
resource "google_network_security_authz_policy" "model_armor_policy" {
  count    = length(google_network_services_authz_extension.model_armor_content_authz)
  provider = google-beta
  project  = var.governance_project_id
  location = var.region
  name     = "model-armor-authz-policy-${var.environment}"

  target {
    resources = [google_network_services_agent_gateway.egress_gateway[0].id]
  }

  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  # MCP (streamable HTTP, JSON responses): tool calls and prompt fetches only.
  http_rules {
    to {
      operations {
        methods = ["POST"]
        paths {
          exact = "/mcp"
        }
        mcp {
          base_protocol_methods_option = "SKIP_BASE_PROTOCOL_METHODS"
          methods {
            name = "tools/call"
          }
          methods {
            name = "prompts/get"
          }
        }
      }
    }
  }

  # A2A agent cards.
  http_rules {
    to {
      operations {
        methods = ["GET"]
        paths {
          exact = "/v1/card"
        }
      }
    }
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor_content_authz[0].id]
    }
  }
}

# Google creates the Network Security service agent lazily. Force it into existence so the
# grant below doesn't race it on a fresh governance project.
resource "google_project_service_identity" "networksecurity" {
  count    = var.enable_agent_gateway ? 1 : 0
  provider = google-beta
  project  = var.governance_project_id
  service  = "networksecurity.googleapis.com"
}

resource "google_project_iam_member" "networksecurity_model_armor" {
  count   = var.enable_agent_gateway ? 1 : 0
  project = var.governance_project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:${google_project_service_identity.networksecurity[0].email}"
}

resource "google_project_iam_member" "agentgateway_model_armor" {
  count   = var.enable_agent_gateway ? 1 : 0
  project = var.governance_project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:service-${data.google_project.governance.number}@gcp-sa-agentgateway.iam.gserviceaccount.com"
}

# 5. Google API entries of the central Agent Registry, from the catalog (`google_apis` in
#    catalog/.resolved/<env>.json): only the APIs the agents declare (the policy baseline plus each
#    agent's own google_apis), with their real hostnames. The gateway denies any host that isn't
#    registered, so an API no agent declares can't be reached at all. gRPC clients send host:443 as
#    :authority, so each entry registers both https://<host> and https://<host>:443.
locals {
  google_api_endpoints = var.enable_agent_gateway ? {
    for sid, e in var.google_api_endpoints : replace(sid, "{region}", var.region) => {
      display_name     = e.display_name
      description      = e.description
      url              = replace(e.url, "{region}", var.region)
      protocol_binding = e.protocol_binding
      grant            = e.grant
    }
  } : {}

  # Entries layer 4 grants (grant = baseline): every agent runtime, so their policy lists the agent
  # projects. The other entries (grant = callers) are granted per agent by the layer 5 iap-egress
  # unit, from the catalog google_apis of each agent.
  baseline_endpoint_ids = sort([for sid, e in local.google_api_endpoints : sid if e.grant == "baseline"])
}

resource "google_agent_registry_service" "system_endpoints" {
  for_each     = local.google_api_endpoints
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = each.key
  display_name = each.value.display_name
  # Why the entry exists and which agents declare the API (catalog-compiled, see GOOGLE_APIS).
  description = each.value.description
  interfaces {
    url              = each.value.url
    protocol_binding = each.value.protocol_binding
  }

  # gRPC clients send host:443 as :authority; register both forms.
  interfaces {
    url              = "${each.value.url}:443"
    protocol_binding = each.value.protocol_binding
  }

  endpoint_spec {
    type = "NO_SPEC"
  }

  lifecycle {
    precondition {
      condition     = can(regex("^https://[a-z0-9.-]+\\.googleapis\\.com$", each.value.url))
      error_message = "Google API entry ${each.key}: url ${each.value.url} must be https://<host>.googleapis.com (regenerate catalog/.resolved/<env>.json)."
    }
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
  member    = startswith(each.value, "serviceAccount:") || startswith(each.value, "principalSet:") || startswith(each.value, "principal:") ? each.value : "serviceAccount:${each.value}"
}

data "google_project" "agent_projects" {
  for_each   = toset(var.agent_project_ids)
  project_id = each.value
}

locals {
  # Members of the roles/iap.egressor policy grant_iap_egress.sh writes on each baseline Google API
  # entry. IAP egress authorizes the caller's Agent Identity, so the agent projects' principalSets
  # are what matter here (per project, not per engine: a replaced engine calls the baseline APIs at
  # start-up, before the iap-egress unit grants its new principal). No Vertex AI / Agent Runtime
  # service agent: with Agent Identity an engine egresses as itself, and platform work never
  # crosses this gateway. The agent and test VM service accounts in var.agent_invoker_sa_emails
  # are not on this list either (they only read the CA secret above).
  dynamic_agent_members = flatten([
    for proj_id, p in data.google_project.agent_projects : [
      "principalSet://agents.global.${p.org_id != "" ? "org-${p.org_id}" : "proj-${p.number}"}.system.id.goog/attribute.platformContainer/aiplatform/projects/${p.number}",
      "principalSet://agents.global.${p.org_id != "" ? "org-${p.org_id}" : "proj-${p.number}"}.system.id.goog/attribute.container/projects/${p.number}"
    ]
  ])

  iap_egress_members = distinct([for m in local.dynamic_agent_members : m if m != ""])
}

# 8. Grant roles/iap.egressor on each baseline Google API entry (never on the registry root: a root
#    grant is inherited by every entry, including external endpoints, agents and MCP servers, and IAM
#    allow policies only add). The script writes and verifies every per-entry policy first, then
#    clears the registry-root binding earlier versions wrote. The Google APIs single agents declare
#    (grant = callers), and the agents, MCP servers and external endpoints, are granted by layer 5
#    (unit iap-egress) from the service catalog.
resource "null_resource" "grant_iap_egress" {
  count      = var.enable_agent_gateway && length(local.baseline_endpoint_ids) > 0 && length(var.agent_project_ids) > 0 ? 1 : 0
  depends_on = [google_agent_registry_service.system_endpoints]

  triggers = {
    services_hash             = md5(jsonencode(local.baseline_endpoint_ids))
    members_hash              = md5(jsonencode(local.iap_egress_members))
    gateway_id                = try(google_network_services_agent_gateway.egress_gateway[0].id, "")
    governance_project_number = data.google_project.governance.number
    version                   = "4"
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../../_shared/scripts/grant_iap_egress.sh"
    environment = {
      IAP_MODE                 = "system-endpoints"
      GOVERNANCE_PROJECT_ID    = var.governance_project_id
      REGION                   = var.region
      IAP_MEMBERS_JSON         = jsonencode(local.iap_egress_members)
      SYSTEM_ENDPOINT_IDS_JSON = jsonencode(local.baseline_endpoint_ids)
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
