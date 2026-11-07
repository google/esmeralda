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
#
# Adapted from github.com/ricardosmh/apigeex-terraform-deployment (Apache-2.0),
# upgraded to Esmeralda's Private Service Connect (non-VPC-peering) architecture.

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  analytics_region = coalesce(var.analytics_region, var.region)
  use_cmek         = var.enable_cmek && !var.byo_apigee_org && var.billing_type != "EVALUATION"

  org_id = var.byo_apigee_org ? var.existing_org_id : google_apigee_organization.org[0].id
  instance_service_attachment = (
    var.byo_apigee_org
    ? var.existing_instance_service_attachment
    : google_apigee_instance.instance[0].service_attachment
  )

  consumer_accept_list = length(var.consumer_accept_list) > 0 ? var.consumer_accept_list : [var.project_id]
  apigee_service_agent = "service-${data.google_project.current.number}@gcp-sa-apigee.iam.gserviceaccount.com"
}

# ====================================================================
# 1. Optional CMEK Keys (Org Runtime DB + Instance Disk)
# ====================================================================

resource "google_kms_key_ring" "org" {
  count    = local.use_cmek ? 1 : 0
  project  = var.project_id
  name     = "apigee-org-keyring-${var.environment}"
  location = local.analytics_region
}

resource "google_kms_crypto_key" "org_db" {
  count    = local.use_cmek ? 1 : 0
  name     = "apigee-org-db-key-${var.environment}"
  key_ring = google_kms_key_ring.org[0].id
}

resource "google_kms_crypto_key_iam_member" "org_db" {
  count         = local.use_cmek ? 1 : 0
  crypto_key_id = google_kms_crypto_key.org_db[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${local.apigee_service_agent}"
}

resource "google_kms_key_ring" "instance" {
  count    = local.use_cmek ? 1 : 0
  project  = var.project_id
  name     = "apigee-instance-keyring-${var.environment}"
  location = var.region
}

resource "google_kms_crypto_key" "instance_disk" {
  count    = local.use_cmek ? 1 : 0
  name     = "apigee-instance-disk-key-${var.environment}"
  key_ring = google_kms_key_ring.instance[0].id
}

resource "google_kms_crypto_key_iam_member" "instance_disk" {
  count         = local.use_cmek ? 1 : 0
  crypto_key_id = google_kms_crypto_key.instance_disk[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${local.apigee_service_agent}"
}

# ====================================================================
# 2. Apigee Organization & Add-ons (Non-Peering PSC Architecture)
# ====================================================================

resource "google_apigee_organization" "org" {
  count = var.byo_apigee_org ? 0 : 1

  project_id          = var.project_id
  analytics_region    = local.analytics_region
  runtime_type        = "CLOUD"
  billing_type        = var.billing_type
  disable_vpc_peering = true
  retention           = var.billing_type == "EVALUATION" ? null : "MINIMUM"

  runtime_database_encryption_key_name = local.use_cmek ? google_kms_crypto_key.org_db[0].id : null

  depends_on = [
    google_kms_crypto_key_iam_member.org_db,
  ]
}

resource "google_apigee_addons_config" "org_addons" {
  count = var.byo_apigee_org ? 0 : 1
  org   = google_apigee_organization.org[0].name

  addons_config {
    advanced_api_ops_config {
      enabled = var.enable_analytics
    }
  }
}

# ====================================================================
# 3. Runtime Instance, Environment & Environment Group
# ====================================================================

resource "google_apigee_instance" "instance" {
  count = var.byo_apigee_org ? 0 : 1

  org_id                   = local.org_id
  name                     = "apigee-instance-${var.environment}"
  location                 = var.region
  consumer_accept_list     = local.consumer_accept_list
  disk_encryption_key_name = local.use_cmek ? google_kms_crypto_key.instance_disk[0].id : null

  depends_on = [
    google_kms_crypto_key_iam_member.instance_disk,
  ]
}

resource "google_apigee_environment" "env" {
  org_id            = local.org_id
  name              = var.apigee_env_name
  display_name      = "Esmeralda ${var.environment} Apigee Environment"
  description       = "Apigee environment for Esmeralda API-management capabilities (${var.environment})"
  type              = var.billing_type == "EVALUATION" ? null : var.environment_type
  forward_proxy_uri = null
}

resource "google_apigee_environment_addons_config" "analytics" {
  count = var.enable_analytics && var.billing_type != "EVALUATION" ? 1 : 0

  env_id            = google_apigee_environment.env.id
  analytics_enabled = var.enable_analytics
}

resource "google_apigee_envgroup" "envgroup" {
  org_id    = local.org_id
  name      = "${var.envgroup_name}-${var.environment}"
  hostnames = var.envgroup_hostnames
}

resource "google_apigee_envgroup_attachment" "env_to_group" {
  envgroup_id = google_apigee_envgroup.envgroup.id
  environment = google_apigee_environment.env.name
}

resource "google_apigee_instance_attachment" "env_to_instance" {
  count = var.byo_apigee_org ? 0 : 1

  instance_id = google_apigee_instance.instance[0].id
  environment = google_apigee_environment.env.name
}

# ====================================================================
# 4. Northbound Entrypoint Contract (ALB -> PSC NEG -> Apigee)
# ====================================================================

resource "google_compute_region_network_endpoint_group" "apigee_psc" {
  name                  = "neg-apigee-psc-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "PRIVATE_SERVICE_CONNECT"
  psc_target_service    = local.instance_service_attachment
  network               = var.vpc_id
  subnetwork            = var.subnet_id
}

resource "google_compute_region_backend_service" "apigee" {
  name                  = "backend-apigee-gateway-${var.environment}"
  project               = var.project_id
  region                = var.region
  load_balancing_scheme = "INTERNAL_MANAGED"
  protocol              = "HTTPS"
  timeout_sec           = 300

  backend {
    group          = google_compute_region_network_endpoint_group.apigee_psc.id
    balancing_mode = "UTILIZATION"
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# ====================================================================
# 5. Southbound Entrypoint Target Server (Apigee -> Entrypoint ILB)
# ====================================================================

resource "google_apigee_endpoint_attachment" "entrypoint" {
  count = var.entrypoint_service_attachment != null ? 1 : 0

  org_id                 = local.org_id
  endpoint_attachment_id = "ea-esmeralda-entrypoint-${var.environment}"
  location               = var.region
  service_attachment     = var.entrypoint_service_attachment
}

resource "google_apigee_target_server" "entrypoint" {
  count = var.entrypoint_service_attachment != null ? 1 : 0

  name   = "ts-esmeralda-entrypoint"
  env_id = google_apigee_environment.env.id
  host   = google_apigee_endpoint_attachment.entrypoint[0].host
  port   = 443

  s_sl_info {
    enabled                  = true
    enforce                  = true
    ignore_validation_errors = false
  }
}

# ====================================================================
# 6. Proxy Runtime Service Account & TokenCreator IAM
# ====================================================================

resource "google_service_account" "apigee_proxy_runtime" {
  project      = var.project_id
  account_id   = "sa-esmeralda-apigee-proxy-${var.environment}"
  display_name = "Esmeralda Apigee Proxy Runtime SA (${var.environment})"
  description  = "Service account used by Apigee proxies to mint upstream GoogleAccessToken and GoogleIDToken credentials."
}

resource "google_service_account_iam_member" "apigee_service_agent_token_creator" {
  service_account_id = google_service_account.apigee_proxy_runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${local.apigee_service_agent}"
}
