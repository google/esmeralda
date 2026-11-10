# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

terraform {
  required_version = ">= 1.3"
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

locals {
  org_name    = trimprefix(var.org_id, "organizations/")
  bundle_root = coalesce(var.bundle_source_dir, "${path.module}/../../../../../apps/apigee/proxies/partner-mcp")

  partner_exposed_mcp = {
    for name, entry in var.catalog : name => entry
    if try(entry.expose_to_partners, false) && try(entry.kind, "mcp") == "mcp"
  }

  allowed_mcp_services = sort(keys(local.partner_exposed_mcp))
}

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = local.bundle_root
  output_path = "${path.module}/.terraform/tmp/partner-mcp.zip"
}

resource "google_apigee_environment_keyvaluemaps" "partner_mcp" {
  env_id = var.apigee_env_id
  name   = "kvm-partner-mcp"
}

resource "google_apigee_environment_keyvaluemaps_entries" "allowed_mcp_services" {
  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.partner_mcp.id
  name               = "allowed_mcp_services"
  value              = length(local.allowed_mcp_services) > 0 ? join(",", local.allowed_mcp_services) : "corporate-email,income-verification"
}

# Internal domain of the entry point hosts (<service>.<apex_domain>): Host header and ID token audience.
resource "google_apigee_environment_keyvaluemaps_entries" "apex_domain" {
  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.partner_mcp.id
  name               = "apex_domain"
  value              = var.apex_domain
}

resource "google_apigee_api" "partner_mcp" {
  org_id         = local.org_name
  name           = var.proxy_name
  config_bundle  = data.archive_file.bundle.output_path
  detect_md5hash = data.archive_file.bundle.output_md5
}

resource "google_apigee_environment_api_revision_deployment" "partner_mcp" {
  org_id          = local.org_name
  environment     = var.apigee_env_name
  api             = google_apigee_api.partner_mcp.name
  revision        = tonumber(google_apigee_api.partner_mcp.latest_revision_id)
  override        = true
  service_account = var.service_account

  depends_on = [
    google_apigee_environment_keyvaluemaps_entries.allowed_mcp_services,
    google_apigee_environment_keyvaluemaps_entries.apex_domain,
    google_apigee_target_server.mcp,
  ]
}

# ====================================================================
# Southbound path: Apigee -> endpoint attachment -> PSC service attachment -> entry point ILB
# --------------------------------------------------------------------
# The MCP servers only accept traffic from internal load balancers (Cloud Run ingress
# internal-and-cloud-load-balancing), so the proxy reaches them through the internal entry point,
# like any agent: Host <service>.<domain>, TLS with the internal Root CA, Google ID token with
# audience https://<service>.<domain> (Cloud Run custom audience). Apigee X (no VPC peering) reaches
# the VPC only through an endpoint attachment to a PSC service attachment, published here on the
# entry point forwarding rule (layer 5 entrypoint unit) with its own PSC NAT subnet.
# ====================================================================

locals {
  southbound           = var.southbound_enabled
  forwarding_rule_name = coalesce(var.entrypoint_forwarding_rule_name, "ilb-esmeralda-rule-${var.environment}")
  internal_ca_keystore = "ks-esmeralda-internal-ca"
  # A name covered by the entry point certificate (*.<domain>) that resolves, for Apigee only, to the
  # endpoint attachment IP: TLS hostname verification stays strict.
  southbound_hostname = "${var.southbound_host_label}.${var.apex_domain}"
  mcp_target_host     = local.southbound ? local.southbound_hostname : var.mcp_target_host
  network_name        = var.network_id == null ? null : element(split("/", var.network_id), length(split("/", var.network_id)) - 1)
}

data "google_project" "gateway" {
  count      = local.southbound ? 1 : 0
  project_id = var.project_id
}

data "google_compute_forwarding_rule" "entrypoint" {
  count   = local.southbound ? 1 : 0
  project = var.project_id
  region  = var.region
  name    = local.forwarding_rule_name
}

resource "google_compute_subnetwork" "psc_nat" {
  count         = local.southbound ? 1 : 0
  name          = "sb-esmeralda-psc-nat-apigee-${var.environment}"
  project       = var.network_project_id
  region        = var.region
  network       = var.network_id
  ip_cidr_range = var.psc_nat_cidr
  purpose       = "PRIVATE_SERVICE_CONNECT"
}

resource "google_compute_service_attachment" "entrypoint" {
  count       = local.southbound ? 1 : 0
  name        = "sa-esmeralda-entrypoint-apigee-${var.environment}"
  project     = var.project_id
  region      = var.region
  description = "Publishes the internal entry point to the Apigee runtime (southbound partner-mcp calls)."

  target_service        = data.google_compute_forwarding_rule.entrypoint[0].self_link
  connection_preference = "ACCEPT_AUTOMATIC"
  nat_subnets           = [google_compute_subnetwork.psc_nat[0].id]
  enable_proxy_protocol = false
}

resource "google_apigee_endpoint_attachment" "entrypoint" {
  count                  = local.southbound ? 1 : 0
  org_id                 = "organizations/${local.org_name}"
  endpoint_attachment_id = "ea-esmeralda-mcp-${var.environment}"
  location               = var.region
  service_attachment     = google_compute_service_attachment.entrypoint[0].id
}

# --- DNS: <southbound_host_label>.<domain> -> endpoint attachment IP, resolved by Apigee through
# DNS peering with a private zone of the Shared VPC (the name only answers inside that zone).
resource "google_dns_managed_zone" "southbound" {
  count       = local.southbound ? 1 : 0
  project     = var.network_project_id
  name        = "apigee-southbound-${var.environment}"
  dns_name    = "${local.southbound_hostname}."
  description = "Apigee southbound name of the entry point (endpoint attachment IP), peered to the Apigee org."
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = var.network_id
    }
  }
}

resource "google_dns_record_set" "southbound" {
  count        = local.southbound ? 1 : 0
  project      = var.network_project_id
  managed_zone = google_dns_managed_zone.southbound[0].name
  name         = google_dns_managed_zone.southbound[0].dns_name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_apigee_endpoint_attachment.entrypoint[0].host]
}

resource "google_project_iam_member" "apigee_dns_peer" {
  count   = local.southbound ? 1 : 0
  project = var.network_project_id
  role    = "roles/dns.peer"
  member  = "serviceAccount:service-${data.google_project.gateway[0].number}@gcp-sa-apigee.iam.gserviceaccount.com"
}

resource "google_apigee_dns_zone" "southbound" {
  count       = local.southbound ? 1 : 0
  org_id      = "organizations/${local.org_name}"
  dns_zone_id = "dz-esmeralda-mcp-${var.environment}"
  domain      = "${local.southbound_hostname}."
  description = "Resolves the partner-mcp southbound name through the Shared VPC private zone."

  peering_config {
    target_project_id = var.network_project_id
    target_network_id = local.network_name
  }

  depends_on = [
    google_project_iam_member.apigee_dns_peer,
    google_dns_record_set.southbound,
  ]
}

# Truststore with the internal Root CA, so the proxy validates the entry point certificate.
resource "google_apigee_env_keystore" "internal_ca" {
  count  = local.southbound ? 1 : 0
  env_id = var.apigee_env_id
  name   = local.internal_ca_keystore
}

resource "google_apigee_keystores_aliases_key_cert_file" "internal_ca" {
  count       = local.southbound ? 1 : 0
  org_id      = local.org_name
  environment = var.apigee_env_name
  keystore    = google_apigee_env_keystore.internal_ca[0].name
  alias       = "internal-root-ca"
  cert        = var.internal_ca_cert_pem
}

resource "google_apigee_target_server" "mcp" {
  name        = var.mcp_target_server_name
  env_id      = var.apigee_env_id
  description = "Internal entry point (via PSC) for partner-mcp: one host per MCP server, <service>.${var.apex_domain}."
  host        = local.mcp_target_host
  port        = 443
  protocol    = "HTTP"

  s_sl_info {
    enabled                  = true
    enforce                  = true
    ignore_validation_errors = false
    trust_store              = local.southbound ? google_apigee_keystores_aliases_key_cert_file.internal_ca[0].keystore : var.mcp_target_trust_store
  }

  lifecycle {
    precondition {
      condition     = local.mcp_target_host != null && local.mcp_target_host != ""
      error_message = "partner-mcp needs a southbound target: set southbound_enabled = true (PSC to the entry point) or mcp_target_host."
    }
  }

  depends_on = [google_apigee_dns_zone.southbound]
}
