# infrastructure/modules/5-governance/modules/6_agent_gateway/main.tf
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
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
  }
}

data "google_project" "governance" {
  project_id = var.governance_project_id
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

# 2. Central Agent Gateway Resource (AGENT_TO_ANYWHERE Egress Mode)
resource "google_network_services_agent_gateway" "egress_gateway" {
  count    = var.enable_agent_gateway && var.subnet_self_link != "" ? 1 : 0
  provider = google-beta
  project  = var.governance_project_id
  location = var.region
  name     = "esmeralda-agent-egress-gateway-${var.environment}"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${data.google_project.governance.number}/locations/${var.region}"
  ]

  network_config {
    egress {
      network_attachment = google_compute_network_attachment.agent_gateway[0].id
    }

    # Strict domain peering for private microservices (never wildcard or googleapis.com)
    dns_peering_config {
      domains        = ["esmeralda.internal."]
      target_project = var.net_host_project_id
      target_network = startswith(var.vpc_name, "projects/") ? var.vpc_name : "projects/${var.net_host_project_id}/global/networks/${var.vpc_name}"
    }
  }

  depends_on = [google_project_iam_member.agent_gateway_dns_peer]
}

# 3. Model Armor Authorization Extension (CONTENT_AUTHZ) - Optional
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
locals {
  google_apis = {
    aiplatform             = "Vertex AI Platform"
    modelarmor             = "Model Armor"
    cloudresourcemanager   = "Cloud Resource Manager"
    logging                = "Logging"
    monitoring             = "Monitoring"
    telemetry              = "Telemetry"
    agentregistry          = "Agent Registry"
    iap                    = "Identity-Aware Proxy"
    iamcredentials         = "IAM Credentials"
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
    protocol_binding = "HTTP_JSON"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

# 6. Central Gateway CA Root Certificate saved to Secret Manager
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
  secret_data = join("\n\n", google_network_services_agent_gateway.egress_gateway[0].agent_gateway_card[0].root_certificates)
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
}

# 8. Grant roles/iap.egressor at Registry-wide and Per-endpoint scopes via official gcloud iap command
resource "null_resource" "grant_iap_egress" {
  count      = var.enable_agent_gateway ? 1 : 0
  depends_on = [google_agent_registry_service.system_endpoints]

  triggers = {
    services_hash = md5(jsonencode(local.system_endpoints))
    members_hash  = md5(jsonencode(local.cleaned_iap_members))
    version       = "4"
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "🔒 Setting Registry-wide IAP Egress Policy on Central Governance Agent Registry (${var.governance_project_id})..."
      
      cat << 'EOF' > /tmp/agent_registry_iap_policy.json
{
  "bindings": [
    {
      "role": "roles/iap.egressor",
      "members": ${jsonencode([for m in local.cleaned_iap_members : startswith(m, "serviceAccount:") || startswith(m, "principalSet:") || startswith(m, "principal:") ? m : "serviceAccount:${m}"])}
    }
  ]
}
EOF

      echo "  -> Applying Registry-wide policy..."
      gcloud iap web set-iam-policy /tmp/agent_registry_iap_policy.json \
        --project=${var.governance_project_id} \
        --resource-type=agent-registry \
        --region=${var.region} \
        --quiet

      echo "  -> Discovering endpoints in Agent Registry..."
      TOKEN="$(gcloud auth print-access-token)"
      ENDPOINTS_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" "https://agentregistry.googleapis.com/v1alpha/projects/${var.governance_project_id}/locations/${var.region}/endpoints")

      for ENDPOINT_ID in $(echo "$ENDPOINTS_JSON" | jq -r '.endpoints[]?.name | split("/") | last'); do
        if [ "$ENDPOINT_ID" != "null" ] && [ -n "$ENDPOINT_ID" ]; then
          echo "  -> Setting IAP egress policy on endpoint: $ENDPOINT_ID..."
          gcloud iap web set-iam-policy /tmp/agent_registry_iap_policy.json \
            --project=${var.governance_project_id} \
            --resource-type=agent-registry \
            --endpoint="$ENDPOINT_ID" \
            --region=${var.region} \
            --quiet
        fi
      done

      rm -f /tmp/agent_registry_iap_policy.json
      echo "✅ Central Agent Gateway IAP egress policies applied successfully across all endpoints."
    EOT
  }
}
