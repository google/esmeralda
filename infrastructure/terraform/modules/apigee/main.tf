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

/**
 * Copyright 2025 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * Apigee Primitive Module
 *
 * A standalone, reusable module for Apigee organization management.
 * Creates Apigee organization, environments, environment groups, and instances.
 *
 * This module is designed to be used independently or composed by wrapper modules
 * like semantic-cache.
 *
 * Key features:
 * - Object-based configuration for clean variable structure
 * - Supports both VPC peering and Non-VPC Peering (PSC) modes
 * - Environment properties support for service extensions
 * - APIM Operator Workload Identity support
 * - DNS peering zone configuration
 */

locals {
  # Apigee service agent (per-project service account)
  # Format: service-{PROJECT_NUMBER}@gcp-sa-apigee.iam.gserviceaccount.com
  apigee_service_agent = "serviceAccount:service-${var.project_number}@gcp-sa-apigee.iam.gserviceaccount.com"

  # Apigee tenant project ID (e.g., "m62d5b515e41e50e6-tp")
  # This is the project Apigee uses for PSC connections, different from the main project
  apigee_tenant_project_id = try(module.apigee.organization.apigee_project_id, null)

  # Strip environments from instances to prevent the Fabric module from creating
  # instance attachments. We create them separately below with proper dependencies
  # on our own google_apigee_environment resources.
  instances_without_environments = {
    for k, v in var.instances : k => merge(v, { environments = [] })
  }

  # DNS zone serialization: Apigee holds an org-level lock during DNS zone creation,
  # so parallel creates (via for_each) cause the second to fail. We sort the keys,
  # create the first zone alone, then create the rest with depends_on.
  dns_zone_keys_sorted = sort(keys(var.dns_peering_zones))
  dns_first_zone_key   = length(local.dns_zone_keys_sorted) > 0 ? local.dns_zone_keys_sorted[0] : null
  dns_remaining_zones = {
    for k in local.dns_zone_keys_sorted : k => var.dns_peering_zones[k]
    if k != local.dns_first_zone_key
  }

  # Build instance-to-environment attachment map from the original instances variable
  instance_environment_attachments = merge(flatten([
    for instance_key, instance in var.instances : [
      for env in coalesce(instance.environments, []) : {
        "${instance_key}-${env}" = {
          instance    = instance_key
          environment = env
        }
      }
    ]
  ])...)
}

# ==============================================================================
# APIGEE ORGANIZATION
# ==============================================================================

module "apigee" {
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/apigee?ref=v53.0.0"
  project_id = var.project_id

  organization = {
    display_name            = var.organization.display_name
    description             = var.organization.description
    billing_type            = var.organization.billing_type
    analytics_region        = coalesce(var.organization.analytics_region, var.region)
    authorized_network      = var.organization.disable_vpc_peering ? null : var.vpc_id
    runtime_type            = var.organization.runtime_type
    database_encryption_key = var.organization.database_encryption_key
    disable_vpc_peering     = var.organization.disable_vpc_peering
  }

  envgroups = var.envgroups

  # Environments created separately using google_apigee_environment to support properties
  environments = {}

  # Pass instances WITHOUT environments to prevent the Fabric module from creating
  # instance attachments. We create them separately with proper dependencies.
  instances = local.instances_without_environments

  endpoint_attachments = var.endpoint_attachments

  # DNS zones managed separately for dependency ordering
  dns_zones = {}
}

# ==============================================================================
# APIGEE ENVIRONMENTS
# ==============================================================================

# Create Apigee environments using google_apigee_environment directly
# This allows setting properties like apigee-service-extension-enabled
resource "google_apigee_environment" "environments" {
  for_each = var.environments

  org_id       = module.apigee.org_id
  name         = each.key
  display_name = each.value.display_name
  description  = each.value.description
  type         = each.value.type

  dynamic "node_config" {
    for_each = each.value.node_config != null ? [each.value.node_config] : []
    content {
      min_node_count = node_config.value.min_node_count
      max_node_count = node_config.value.max_node_count
    }
  }

  dynamic "properties" {
    for_each = each.value.properties != null ? [each.value.properties] : []
    content {
      dynamic "property" {
        for_each = properties.value
        content {
          name  = property.key
          value = property.value
        }
      }
    }
  }

  depends_on = [module.apigee]
}

# Create environment-to-envgroup attachments
resource "google_apigee_envgroup_attachment" "envgroup_attachments" {
  for_each = {
    for pair in flatten([
      for env_name, env in var.environments : [
        for envgroup in env.envgroups : {
          key      = "${env_name}-${envgroup}"
          env_name = env_name
          envgroup = envgroup
        }
      ]
    ]) : pair.key => pair
  }

  envgroup_id = module.apigee.envgroups[each.value.envgroup].id
  environment = google_apigee_environment.environments[each.value.env_name].name

  depends_on = [google_apigee_environment.environments]
}

# ==============================================================================
# INSTANCE ATTACHMENTS
# ==============================================================================

# Create instance-to-environment attachments separately from the Fabric module
# This ensures proper dependency ordering: environments must exist before attachments
resource "google_apigee_instance_attachment" "instance_attachments" {
  for_each = local.instance_environment_attachments

  instance_id = module.apigee.instances[each.value.instance].id
  environment = each.value.environment

  depends_on = [
    google_apigee_environment.environments,
    module.apigee
  ]
}
# ==============================================================================
# SERVICE ACCOUNTS
# ==============================================================================

# Service Account for Apigee proxy runtime operations
resource "google_service_account" "proxy_runtime" {
  count        = var.create_service_accounts ? 1 : 0
  project      = var.project_id
  account_id   = "${var.service_account_prefix}-proxy-runtime"
  display_name = "Apigee Proxy Runtime Service Account"
  description  = "Service account used for Apigee proxy runtime operations and deployments"
}

# Grant AI Platform User role to the proxy runtime service account
resource "google_project_iam_member" "proxy_runtime_aiplatform_user" {
  count   = var.create_service_accounts ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.proxy_runtime[0].email}"

  depends_on = [google_service_account.proxy_runtime]
}

# Grant Service Account User role to the proxy runtime service account.
# Note: Project-level scope is acceptable for this demo. In production,
# scope to specific SAs via google_service_account_iam_member.
resource "google_project_iam_member" "proxy_runtime_sa_user" {
  count   = var.create_service_accounts ? 1 : 0
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.proxy_runtime[0].email}"

  depends_on = [google_service_account.proxy_runtime]
}

# ==============================================================================
# APIM OPERATOR SERVICE ACCOUNT (GSA-based Workload Identity)
# ==============================================================================
# The APIM Operator uses ComputeEngineCredentials which requires a traditional
# GSA-based Workload Identity setup. Direct principal:// grants are NOT supported.
# The KSA must be annotated with: iam.gke.io/gcp-service-account: [GSA-EMAIL]

# GCP Service Account for Apigee APIM Operator
resource "google_service_account" "apim_operator" {
  count        = var.create_apim_operator_iam ? 1 : 0
  project      = var.project_id
  account_id   = "${var.service_account_prefix}-apim-gsa"
  display_name = "Apigee APIM Operator GSA"
  description  = "GCP service account for Apigee APIM Operator - required for GSA-based Workload Identity"
}

# Workload Identity binding - allows Kubernetes SA to impersonate this GCP SA
# Uses serviceAccount: format (not principal://) for compatibility with ComputeEngineCredentials
resource "google_service_account_iam_member" "apim_workload_identity" {
  count              = var.create_apim_operator_iam && var.enable_apim_workload_identity ? 1 : 0
  service_account_id = google_service_account.apim_operator[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.apim_operator_namespace}/${var.apim_operator_ksa}]"

  depends_on = [google_service_account.apim_operator]
}

# Grant Apigee Admin role to the GSA
resource "google_project_iam_member" "apim_apigee_admin" {
  count   = var.create_apim_operator_iam ? 1 : 0
  project = var.project_id
  role    = "roles/apigee.admin"
  member  = "serviceAccount:${google_service_account.apim_operator[0].email}"

  depends_on = [google_service_account.apim_operator]
}

# Grant Network Services Extensions Admin role to the GSA
resource "google_project_iam_member" "apim_extensions_admin" {
  count   = var.create_apim_operator_iam ? 1 : 0
  project = var.project_id
  role    = "roles/networkservices.serviceExtensionsAdmin"
  member  = "serviceAccount:${google_service_account.apim_operator[0].email}"

  depends_on = [google_service_account.apim_operator]
}

# Grant Compute Network Admin role to the GSA
resource "google_project_iam_member" "apim_network_admin" {
  count   = var.create_apim_operator_iam ? 1 : 0
  project = var.project_id
  role    = "roles/compute.networkAdmin"
  member  = "serviceAccount:${google_service_account.apim_operator[0].email}"

  depends_on = [google_service_account.apim_operator]
}

# Grant Compute Load Balancer Admin role to the GSA
resource "google_project_iam_member" "apim_lb_admin" {
  count   = var.create_apim_operator_iam ? 1 : 0
  project = var.project_id
  role    = "roles/compute.loadBalancerAdmin"
  member  = "serviceAccount:${google_service_account.apim_operator[0].email}"

  depends_on = [google_service_account.apim_operator]
}

# ==============================================================================
# NORTHBOUND LOAD BALANCER (PSC)
# ==============================================================================
# Regional internal HTTPS LB fronting Apigee via PSC NEG.
# Ref: https://cloud.google.com/apigee/docs/api-platform/system-administration/northbound-networking-psc

# Static internal IP for the LB frontend
resource "google_compute_address" "northbound_lb" {
  count        = var.northbound_lb != null ? 1 : 0
  name         = "${var.service_account_prefix}-northbound-lb-ip"
  project      = var.project_id
  region       = var.region
  subnetwork   = var.northbound_lb.subnet_self_link
  address_type = "INTERNAL"
  purpose      = "SHARED_LOADBALANCER_VIP"
}

# PSC NEG per Apigee instance region
resource "google_compute_region_network_endpoint_group" "apigee_psc_neg" {
  for_each = var.northbound_lb != null ? var.northbound_lb.instances : {}

  name                  = "${var.service_account_prefix}-psc-neg-${each.key}"
  project               = var.project_id
  region                = each.key
  network_endpoint_type = "PRIVATE_SERVICE_CONNECT"
  network               = var.northbound_lb.network_self_link
  subnetwork            = var.northbound_lb.subnet_self_link
  psc_target_service    = module.apigee.instances[each.key].service_attachment

  depends_on = [module.apigee]
}

# Regional backend service (no health check for PSC NEGs)
resource "google_compute_region_backend_service" "northbound" {
  count = var.northbound_lb != null ? 1 : 0

  name                  = "${var.service_account_prefix}-northbound-backend"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTPS"
  load_balancing_scheme = "INTERNAL_MANAGED"

  dynamic "backend" {
    for_each = var.northbound_lb != null ? var.northbound_lb.instances : {}
    content {
      group          = google_compute_region_network_endpoint_group.apigee_psc_neg[backend.key].id
      balancing_mode = "UTILIZATION"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Regional URL map
resource "google_compute_region_url_map" "northbound" {
  count           = var.northbound_lb != null ? 1 : 0
  name            = "${var.service_account_prefix}-northbound-url-map"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.northbound[0].id
}

# Regional target HTTPS proxy
resource "google_compute_region_target_https_proxy" "northbound" {
  count                            = var.northbound_lb != null ? 1 : 0
  name                             = "${var.service_account_prefix}-northbound-https-proxy"
  project                          = var.project_id
  region                           = var.region
  url_map                          = google_compute_region_url_map.northbound[0].id
  certificate_manager_certificates = [var.northbound_lb.ssl_certificate_id]
}

# Forwarding rule
resource "google_compute_forwarding_rule" "northbound" {
  count                 = var.northbound_lb != null ? 1 : 0
  name                  = "${var.service_account_prefix}-northbound-https-rule"
  project               = var.project_id
  region                = var.region
  ip_address            = google_compute_address.northbound_lb[0].id
  ip_protocol           = "TCP"
  port_range            = "443"
  load_balancing_scheme = "INTERNAL_MANAGED"
  target                = google_compute_region_target_https_proxy.northbound[0].id
  network               = var.northbound_lb.network_self_link
  subnetwork            = var.northbound_lb.subnet_self_link
}

# ==============================================================================
# SOUTHBOUND DNS PEERING
# ==============================================================================
# Allows Apigee to resolve hostnames in customer private DNS zones.
# Ref: https://cloud.google.com/apigee/docs/api-platform/architecture/southbound-networking-patterns-endpoints#peering-disabled

# Grant the Apigee P4SA the dns.peer role so it can create DNS peering zones
resource "google_project_iam_member" "apigee_dns_peer" {
  count   = length(var.dns_peering_zones) > 0 ? 1 : 0
  project = var.project_id
  role    = "roles/dns.peer"
  member  = local.apigee_service_agent
}

# First DNS zone (by sorted key) — created alone to avoid org-level lock contention
resource "google_apigee_dns_zone" "dns_peering_first" {
  count = local.dns_first_zone_key != null ? 1 : 0

  org_id      = module.apigee.org_id
  dns_zone_id = local.dns_first_zone_key
  domain      = var.dns_peering_zones[local.dns_first_zone_key].domain
  description = var.dns_peering_zones[local.dns_first_zone_key].description

  peering_config {
    target_project_id = var.dns_peering_zones[local.dns_first_zone_key].target_project_id
    target_network_id = var.dns_peering_zones[local.dns_first_zone_key].target_network_id
  }

  timeouts {
    create = "10m"
  }

  depends_on = [
    module.apigee,
    google_project_iam_member.apigee_dns_peer
  ]
}

# Remaining DNS zones — created after the first to serialize Apigee API calls
resource "google_apigee_dns_zone" "dns_peering_remaining" {
  for_each = local.dns_remaining_zones

  org_id      = module.apigee.org_id
  dns_zone_id = each.key
  domain      = each.value.domain
  description = each.value.description

  peering_config {
    target_project_id = each.value.target_project_id
    target_network_id = each.value.target_network_id
  }

  timeouts {
    create = "10m"
  }

  depends_on = [
    module.apigee,
    google_apigee_dns_zone.dns_peering_first
  ]
}

# ==============================================================================
# SOUTHBOUND WILDCARD DNS RECORD
# ==============================================================================
# Wildcard A record in the Apigee internal DNS zone pointing to the PSC
# endpoint attachment IP. This allows Apigee proxies to resolve
# *.internal.<domain> to the internal GKE gateway via PSC.

resource "google_dns_record_set" "internal_wildcard" {
  count = var.internal_dns_wildcard != null ? 1 : 0

  project      = var.project_id
  name         = "*.${var.internal_dns_wildcard.domain}"
  managed_zone = var.internal_dns_wildcard.managed_zone
  type         = "A"
  ttl          = var.internal_dns_wildcard.ttl
  rrdatas      = [module.apigee.endpoint_attachment_hosts[var.internal_dns_wildcard.endpoint_attachment]]

  depends_on = [module.apigee]
}
