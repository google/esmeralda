# infrastructure/modules/4-workloads/gateways/agent-gateway/main.tf
# ==============================================================================
# ESMERALDA GCP AGENT GATEWAY MODULE (AGENT_TO_ANYWHERE EGRESS MODE)
# ==============================================================================

data "google_project" "gateway" {
  project_id = var.project_id
}

data "google_project" "net_host" {
  project_id = var.net_host_project_id
}

# 1. Network Attachment for Gateway VPC Egress
resource "google_compute_network_attachment" "agent_gateway" {
  project               = var.project_id
  name                  = "agw-egress-na-${var.environment}"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.subnet_id]
}

# Grant roles/dns.peer to Agent Gateway Service Agent on Shared VPC Host Project (Official GCP Requirement)
resource "google_project_iam_member" "agent_gateway_dns_peer" {
  project = var.net_host_project_id
  role    = "roles/dns.peer"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentgateway.iam.gserviceaccount.com"
}

# 2. Agent Gateway Resource (Egress Gateway mode with Shared VPC DNS Peering)
resource "google_network_services_agent_gateway" "egress_gateway" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "esmeralda-agent-egress-gateway-${var.environment}"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${data.google_project.gateway.number}/locations/${var.region}"
  ]

  network_config {
    egress {
      network_attachment = google_compute_network_attachment.agent_gateway.id
    }

    dns_peering_config {
      domains        = ["esmeralda.internal.", "googleapis.com.", "run.app."]
      target_project = var.net_host_project_id
      target_network = "projects/${var.net_host_project_id}/global/networks/vpc-esmeralda-shared-${var.environment}"
    }
  }

  depends_on = [google_project_iam_member.agent_gateway_dns_peer]
}

# 3. IAP Authorization Extension (REQUEST_AUTHZ)
resource "google_network_services_authz_extension" "iap_request_authz" {
  provider  = google-beta
  project   = var.project_id
  location  = var.region
  name      = "iap-request-authz-ext-${var.environment}"
  service   = "iap.googleapis.com"
  fail_open = true
  timeout   = "1s"

  metadata = {
    iamEnforcementMode = "DRY_RUN"
    iapPolicyVersion   = "V1"
  }
}

# 4. IAP Network Security Authorization Policy (REQUEST_AUTHZ)
resource "google_network_security_authz_policy" "iap_policy" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "iap-agent-authz-policy-${var.environment}"

  target {
    resources = [google_network_services_agent_gateway.egress_gateway.id]
  }

  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.iap_request_authz.id]
    }
  }
}

# 5. Model Armor Authorization Extension (CONTENT_AUTHZ) - Optional
resource "google_network_services_authz_extension" "model_armor_content_authz" {
  count     = var.model_armor_template_name != "" ? 1 : 0
  provider  = google-beta
  project   = var.project_id
  location  = var.region
  name      = "model-armor-authz-ext-${var.environment}"
  service   = "modelarmor.googleapis.com"
  fail_open = false
  timeout   = "2s"

  metadata = {
    templateName = var.model_armor_template_name
  }
}

# 6. Model Armor Network Security Authorization Policy (CONTENT_AUTHZ) - Optional
resource "google_network_security_authz_policy" "model_armor_policy" {
  count    = var.model_armor_template_name != "" ? 1 : 0
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "model-armor-authz-policy-${var.environment}"

  target {
    resources = [google_network_services_agent_gateway.egress_gateway.id]
  }

  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor_content_authz[0].id]
    }
  }
}

# 7. Cross-Project Model Armor IAM Bindings for Agent Gateway Service Account
resource "google_project_iam_member" "gateway_model_armor_user" {
  count   = var.governance_project_id != "" && var.model_armor_template_name != "" ? 1 : 0
  project = var.governance_project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "gateway_model_armor_service_usage" {
  count   = var.governance_project_id != "" && var.model_armor_template_name != "" ? 1 : 0
  project = var.governance_project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "gateway_model_armor_callout" {
  count   = var.model_armor_template_name != "" ? 1 : 0
  project = var.project_id
  role    = "roles/modelarmor.calloutUser"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentplatform.iam.gserviceaccount.com"
}

# 8. Register System Google API Endpoints in Agent Registry (Cloud Networking Solutions Pattern)
locals {
  google_apis = {
    aiplatform             = "Vertex AI Platform"
    cloudresourcemanager   = "Cloud Resource Manager"
    global-discoveryengine = "Global Discovery Engine"
    discoveryengine        = "Discovery Engine"
    logging                = "Logging"
    monitoring             = "Monitoring"
    oauth2                 = "OAuth2"
    telemetry              = "Telemetry"
    trace                  = "Trace"
    agentregistry          = "Agent Registry"
    iap                    = "Identity-Aware Proxy"
    iamcredentials         = "IAM Credentials"
  }

  system_endpoints = merge([
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
  ]...)
}

resource "google_agent_registry_service" "system_endpoints" {
  for_each     = local.system_endpoints
  provider     = google-beta
  project      = var.project_id
  location     = var.region
  service_id   = each.key
  display_name = each.value.display_name

  interfaces {
    url              = each.value.url
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

# 9. Grant roles/iap.egressor at Registry-wide and Per-endpoint scopes (Official GCP Agent Platform Pattern)
resource "null_resource" "grant_iap_egress" {
  depends_on = [google_agent_registry_service.system_endpoints]

  triggers = {
    services_hash = md5(jsonencode(local.system_endpoints))
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "🔒 Granting roles/iap.egressor at Registry-wide & per-endpoint scopes for ${var.project_id}..."
      TOKEN="$(gcloud auth print-access-token)"
      PRINCIPAL="principalSet://agents.global.org-${data.google_project.gateway.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.gateway.number}"
      
      echo "  -> Setting Registry-wide IAP policy..."
      curl -s -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"policy":{"version":3,"bindings":[{"role":"roles/iap.egressor","members":["'"$PRINCIPAL"'","serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com","serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"]}]}}' \
        "https://iap.googleapis.com/v1/projects/${data.google_project.gateway.number}/locations/${var.region}/iap_web/agentRegistry:setIamPolicy"

      ENDPOINTS_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" "https://agentregistry.googleapis.com/v1alpha/projects/${var.project_id}/locations/${var.region}/endpoints")
      
      for ENDPOINT_ID in $(echo "$ENDPOINTS_JSON" | jq -r '.endpoints[]?.name | split("/") | last'); do
        if [ "$ENDPOINT_ID" != "null" ] && [ -n "$ENDPOINT_ID" ]; then
          echo "  -> Setting per-endpoint IAP policy: $ENDPOINT_ID..."
          curl -s -X POST \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"policy":{"version":3,"bindings":[{"role":"roles/iap.egressor","members":["'"$PRINCIPAL"'","serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com","serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"]}]}}' \
            "https://iap.googleapis.com/v1/projects/${data.google_project.gateway.number}/locations/${var.region}/iap_web/agentRegistry/endpoints/$ENDPOINT_ID:setIamPolicy"
        fi
      done
      echo "✅ Agent Gateway IAP egressor policies granted successfully."
    EOT
  }
}

# 10. Agent Gateway Root CA Bundle Secret in Secret Manager
resource "google_secret_manager_secret" "agw_ca_cert" {
  project   = var.project_id
  secret_id = "agw-root-ca-cert-${var.environment}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "agw_ca_cert_latest" {
  secret      = google_secret_manager_secret.agw_ca_cert.id
  secret_data = join("\n\n", google_network_services_agent_gateway.egress_gateway.agent_gateway_card[0].root_certificates)
}

# Secret Manager Access permissions for Reasoning Engine Service Accounts
resource "google_secret_manager_secret_iam_member" "agw_ca_secret_access_re" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.agw_ca_cert.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "agw_ca_secret_access_aiplatform" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.agw_ca_cert.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}




