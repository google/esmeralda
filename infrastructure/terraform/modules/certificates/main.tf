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

# Extract domain name without trailing dot for authorization names
locals {
  domain_name_clean = var.dns_zone_domain != null ? trimsuffix(var.dns_zone_domain, ".") : ""
  domain_auth_name  = var.dns_zone_domain != null ? replace(local.domain_name_clean, ".", "-") : ""
  wildcard_domain   = var.dns_zone_domain != null ? "*.${local.domain_name_clean}" : ""

  # Internal domain configuration - subdomain of dns_zone_domain for validation
  internal_domain        = var.dns_zone_domain != null ? "internal.${local.domain_name_clean}" : ""
  internal_wildcard      = var.dns_zone_domain != null ? "*.${local.internal_domain}" : ""
  internal_auth_name     = "internal-auth"
  internal_auth_regional = "${local.internal_auth_name}-regional"
}

# Regional DNS Authorizations (no map needed for regional gateways)
module "certificate_manager_regional" {
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/certificate-manager?ref=v53.0.0"
  project_id = var.project_id

  count = var.enable_certificate_manager && var.dns_zone_domain != null && var.gateway_scope == "regional" ? 1 : 0

  # No map needed for regional gateways
  map = null

  # Certificate is created directly as a resource below
  certificates = {}

  # DNS authorizations for both public and internal domains
  dns_authorizations = {
    "${local.domain_auth_name}-regional" = {
      location = var.region
      type     = "PER_PROJECT_RECORD"
      domain   = local.domain_name_clean
    }
    (local.internal_auth_regional) = {
      location = var.region
      type     = "PER_PROJECT_RECORD"
      domain   = local.internal_domain
    }
  }
}

# Create the regional certificate directly for public domain
resource "google_certificate_manager_certificate" "regional" {
  count = var.enable_certificate_manager && var.dns_zone_domain != null && var.gateway_scope == "regional" ? 1 : 0

  name        = var.regional_certificate_name
  location    = var.region
  project     = var.project_id
  description = "Regional certificate for ${local.domain_name_clean} and subdomains"

  managed {
    domains = [local.domain_name_clean, local.wildcard_domain]
    dns_authorizations = [
      module.certificate_manager_regional[0].dns_authorizations["${local.domain_auth_name}-regional"].id
    ]
  }

  labels = var.labels
}

# Regional certificate for internal gateways (base domain + wildcard)
# Covers internal.{domain} AND *.internal.{domain}
resource "google_certificate_manager_certificate" "internal_regional" {
  count = var.enable_certificate_manager && var.dns_zone_domain != null && var.gateway_scope == "regional" ? 1 : 0

  name        = var.internal_certificate_name
  location    = var.region
  project     = var.project_id
  description = "Regional certificate for ${local.internal_domain} and ${local.internal_wildcard}"

  managed {
    domains = [local.internal_domain, local.internal_wildcard]
    dns_authorizations = [
      module.certificate_manager_regional[0].dns_authorizations[local.internal_auth_regional].id
    ]
  }

  labels = var.labels
}

# ==============================================================================
# Global DNS Authorization + Certificate (for global load balancers)
# ==============================================================================

# Global DNS Authorization for the public domain
resource "google_certificate_manager_dns_authorization" "global" {
  count = var.enable_certificate_manager && var.dns_zone_domain != null ? 1 : 0

  name        = "${local.domain_auth_name}-global"
  project     = var.project_id
  description = "Global DNS authorization for ${local.domain_name_clean}"
  domain      = local.domain_name_clean
  type        = "FIXED_RECORD"
}

# Global managed certificate covering domain + wildcard
resource "google_certificate_manager_certificate" "global" {
  count = var.enable_certificate_manager && var.dns_zone_domain != null ? 1 : 0

  name        = var.global_certificate_name
  project     = var.project_id
  description = "Global certificate for ${local.domain_name_clean} and subdomains"

  managed {
    domains = [local.domain_name_clean, local.wildcard_domain]
    dns_authorizations = [
      google_certificate_manager_dns_authorization.global[0].id
    ]
  }

  labels = var.labels
}
