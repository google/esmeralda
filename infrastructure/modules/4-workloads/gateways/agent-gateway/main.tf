# infrastructure/modules/4-workloads/gateways/agent-gateway/main.tf
# ==============================================================================
# ESMERALDA GCP AGENT GATEWAY MODULE (AGENT_TO_ANYWHERE EGRESS MODE)
# ==============================================================================

data "google_project" "gateway" {
  project_id = var.project_id
}

# 1. Network Attachment for Gateway VPC Egress
resource "google_compute_network_attachment" "agent_gateway" {
  project               = var.project_id
  name                  = "agw-egress-na-${var.environment}"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.subnet_id]
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
    "//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.region}"
  ]

  network_config {
    egress {
      network_attachment = google_compute_network_attachment.agent_gateway.id
    }

    dns_peering_config {
      domains        = ["esmeralda.internal."]
      target_project = var.net_host_project_id
      target_network = var.vpc_name
    }
  }
}

# 3. IAP Authorization Extension (REQUEST_AUTHZ)
resource "google_network_services_authz_extension" "iap_request_authz" {
  provider  = google-beta
  project   = var.project_id
  location  = var.region
  name      = "iap-request-authz-ext-${var.environment}"
  service   = "iap.googleapis.com"
  fail_open = false
  timeout   = "1s"

  metadata = {
    iamEnforcementMode = "ENFORCE"
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
