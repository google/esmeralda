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

  lifecycle {
    ignore_changes = [network_config]
  }

  depends_on = [google_project_iam_member.agent_gateway_dns_peer]
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
    bigquery               = "BigQuery"
    bigquerystorage        = "BigQuery Storage"
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

# 6. Central Gateway CA Root Certificate saved to Secret Manager
data "google_secret_manager_secret_version" "internal_root_ca" {
  count   = var.enable_agent_gateway && var.gateway_project_id != "" ? 1 : 0
  project = var.gateway_project_id
  secret  = "esmeralda-internal-root-ca-${var.environment}"
}

locals {
  resolved_internal_root_ca_pem = var.internal_root_ca_pem != "" ? var.internal_root_ca_pem : (
    length(data.google_secret_manager_secret_version.internal_root_ca) > 0 ? data.google_secret_manager_secret_version.internal_root_ca[0].secret_data : ""
  )
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
  secret_data = join("\n\n", compact(concat(
    google_network_services_agent_gateway.egress_gateway[0].agent_gateway_card[0].root_certificates,
    [local.resolved_internal_root_ca_pem]
  )))
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
    version       = "5"
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

      echo "  -> Discovering MCP servers in Agent Registry..."
      TOKEN="$(gcloud auth print-access-token)"
      MCPS_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" "https://agentregistry.googleapis.com/v1alpha/projects/${var.governance_project_id}/locations/${var.region}/mcpServers")

      for MCP_ID in $(echo "$MCPS_JSON" | jq -r '.mcpServers[]?.name | split("/") | last'); do
        if [ "$MCP_ID" != "null" ] && [ -n "$MCP_ID" ]; then
          echo "  -> Setting IAP egress policy on mcp-server: $MCP_ID..."
          gcloud iap web set-iam-policy /tmp/agent_registry_iap_policy.json \
            --project=${var.governance_project_id} \
            --resource-type=agent-registry \
            --mcp-server="$MCP_ID" \
            --region=${var.region} \
            --quiet
        fi
      done

      echo "  -> Discovering A2A agents in Agent Registry..."
      AGENTS_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" "https://agentregistry.googleapis.com/v1alpha/projects/${var.governance_project_id}/locations/${var.region}/agents")

      for AGENT_ID in $(echo "$AGENTS_JSON" | jq -r '.agents[]?.name | split("/") | last'); do
        if [ "$AGENT_ID" != "null" ] && [ -n "$AGENT_ID" ]; then
          echo "  -> Setting IAP egress policy on agent: $AGENT_ID..."
          gcloud iap web set-iam-policy /tmp/agent_registry_iap_policy.json \
            --project=${var.governance_project_id} \
            --resource-type=agent-registry \
            --agent="$AGENT_ID" \
            --region=${var.region} \
            --quiet
        fi
      done

      rm -f /tmp/agent_registry_iap_policy.json
      echo "✅ Central Agent Gateway IAP egress policies applied successfully across all endpoints, MCP servers, and agents."
    EOT
  }
}

# 9. Certificate Manager TrustConfig and AgentConnectivityTemplate for Internal Root CA (*.esmeralda.internal)
resource "google_certificate_manager_trust_config" "internal_trust_config" {
  count       = var.enable_agent_gateway && local.resolved_internal_root_ca_pem != "" ? 1 : 0
  project     = var.governance_project_id
  location    = var.region
  name        = "esmeralda-internal-trust-${var.environment}"
  description = "TrustConfig for *.esmeralda.internal private endpoints"

  trust_stores {
    trust_anchors {
      pem_certificate = local.resolved_internal_root_ca_pem
    }
  }

  depends_on = [google_project_service.certificatemanager]
}

resource "null_resource" "configure_egress_trust_config" {
  count      = var.enable_agent_gateway && local.resolved_internal_root_ca_pem != "" && length(google_network_services_agent_gateway.egress_gateway) > 0 ? 1 : 0
  depends_on = [
    google_network_services_agent_gateway.egress_gateway,
    google_certificate_manager_trust_config.internal_trust_config
  ]

  triggers = {
    gateway_id       = google_network_services_agent_gateway.egress_gateway[0].id
    trust_config_id  = google_certificate_manager_trust_config.internal_trust_config[0].id
    root_ca_pem_md5  = md5(local.resolved_internal_root_ca_pem)
    version          = "5"
  }

  provisioner "local-exec" {
    command = <<EOT
      set -e
      echo "🔐 Creating/Updating AgentConnectivityTemplate with Certificate Manager TrustConfig..."
      TOKEN="$(gcloud auth print-access-token)"
      ACT_ID="esmeralda-act-${var.environment}"
      ACT_FULL_NAME="projects/${var.governance_project_id}/locations/${var.region}/agentConnectivityTemplates/$ACT_ID"
      ACT_NUM_NAME="projects/${data.google_project.governance.number}/locations/${var.region}/agentConnectivityTemplates/$ACT_ID"

      cat << 'EOF' > /tmp/agw_act_payload.json
{
  "egressNetworkConfig": {
    "networkAttachment": "${google_compute_network_attachment.agent_gateway[0].id}",
    "dnsPeeringConfig": {
      "domain": "esmeralda.internal.",
      "targetNetwork": "${startswith(var.vpc_name, "projects/") ? var.vpc_name : "projects/${var.net_host_project_id}/global/networks/${var.vpc_name}"}"
    },
    "tlsConfig": {
      "trustConfig": "projects/${var.governance_project_id}/locations/${var.region}/trustConfigs/${google_certificate_manager_trust_config.internal_trust_config[0].name}",
      "additionalRoots": "PUBLICLY_TRUSTED_ROOTS"
    }
  }
}
EOF

      # Check if ACT already exists
      EXISTING_ACT=$(curl -s -H "Authorization: Bearer $TOKEN" "https://networkservices.googleapis.com/v1/$ACT_FULL_NAME" | jq -r '.name // empty')
      if [ -z "$EXISTING_ACT" ]; then
        echo "  -> Creating new AgentConnectivityTemplate ($ACT_ID)..."
        ACT_RESP=$(curl -s -X POST \
          -H "Authorization: Bearer $TOKEN" \
          -H "Content-Type: application/json" \
          -d @/tmp/agw_act_payload.json \
          "https://networkservices.googleapis.com/v1/projects/${var.governance_project_id}/locations/${var.region}/agentConnectivityTemplates?agentConnectivityTemplateId=$ACT_ID")
      else
        echo "  -> Updating existing AgentConnectivityTemplate ($ACT_ID)..."
        ACT_RESP=$(curl -s -X PATCH \
          -H "Authorization: Bearer $TOKEN" \
          -H "Content-Type: application/json" \
          -d @/tmp/agw_act_payload.json \
          "https://networkservices.googleapis.com/v1/$ACT_FULL_NAME?updateMask=egressNetworkConfig.tlsConfig,egressNetworkConfig.dnsPeeringConfig")
      fi
      rm -f /tmp/agw_act_payload.json

      ACT_OP=$(echo "$ACT_RESP" | jq -r '.name // empty')
      if [ -n "$ACT_OP" ]; then
        echo "  -> Waiting for ACT operation $ACT_OP..."
        for i in {1..60}; do
          OP_STATUS=$(curl -s -H "Authorization: Bearer $TOKEN" "https://networkservices.googleapis.com/v1/$ACT_OP")
          DONE=$(echo "$OP_STATUS" | jq -r '.done // false')
          if [ "$DONE" = "true" ]; then
            ERR=$(echo "$OP_STATUS" | jq -r '.error // empty')
            if [ -n "$ERR" ] && [ "$ERR" != "null" ]; then
              echo "❌ ACT Operation failed: $ERR"
              exit 1
            fi
            echo "  -> ACT ready!"
            break
          fi
          sleep 3
        done
      else
        echo "❌ Failed to create/update ACT: $ACT_RESP"
        exit 1
      fi

      echo "🔗 Binding AgentConnectivityTemplate ($ACT_NUM_NAME) to AgentGateway..."
      cat << EOF > /tmp/agw_bind_act.json
{
  "agentConnectivityTemplate": "$ACT_NUM_NAME"
}
EOF

      RESP=$(curl -s -X PATCH \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d @/tmp/agw_bind_act.json \
        "https://networkservices.googleapis.com/v1/projects/${var.governance_project_id}/locations/${var.region}/agentGateways/${google_network_services_agent_gateway.egress_gateway[0].name}?updateMask=agentConnectivityTemplate,networkConfig")
      rm -f /tmp/agw_bind_act.json

      OP_NAME=$(echo "$RESP" | jq -r '.name // empty')
      if [ -n "$OP_NAME" ]; then
        echo "  -> Waiting for AgentGateway update operation $OP_NAME to complete..."
        for i in {1..60}; do
          OP_STATUS=$(curl -s -H "Authorization: Bearer $TOKEN" "https://networkservices.googleapis.com/v1/$OP_NAME")
          DONE=$(echo "$OP_STATUS" | jq -r '.done // false')
          if [ "$DONE" = "true" ]; then
            ERR=$(echo "$OP_STATUS" | jq -r '.error // empty')
            if [ -n "$ERR" ] && [ "$ERR" != "null" ]; then
              echo "❌ AgentGateway Operation failed: $ERR"
              exit 1
            fi
            echo "✅ Agent Gateway Egress TrustConfig & AgentConnectivityTemplate bound successfully!"
            break
          fi
          sleep 5
        done
      else
        echo "❌ Failed to initiate AgentGateway update: $RESP"
        exit 1
      fi
    EOT
  }
}


