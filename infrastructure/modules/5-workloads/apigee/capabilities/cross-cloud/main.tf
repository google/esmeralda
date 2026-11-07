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

# Inbound cross-cloud caller governance module (when governance.capabilities.cross_cloud = "apigee").
# Outbound multi-cloud egress from Esmeralda agents is governed natively by Agent Gateway + Agent Registry.
# This module manages the Apigee KeyValueMap entries for trusted external OIDC/JWT issuers (e.g. AWS IAM OIDC,
# Azure AD / Entra ID) and deploys the cross-cloud shared flow that validates inbound OIDC tokens against
# those JWKS endpoints and propagates a verified caller identity header southbound.

locals {
  org_name    = trimprefix(var.org_id, "organizations/")
  bundle_root = coalesce(var.bundle_source_dir, "${path.module}/../../../../../apps/apigee/sharedflows/cross-cloud")

  flattened_issuer_entries = merge(
    {
      for slug, cfg in var.trusted_issuers : slug => jsonencode(cfg)
    },
    merge([
      for slug, cfg in var.trusted_issuers : {
        "${slug}__issuer_uri" = cfg.issuer_uri
        "${slug}__jwks_uri"   = cfg.jwks_uri
        "${slug}__audience"   = cfg.audience
      }
    ]...)
  )
}

resource "google_apigee_environment_keyvaluemaps" "cross_cloud" {
  env_id = var.apigee_env_id
  name   = "kvm-cross-cloud-issuers"
}

resource "google_apigee_environment_keyvaluemaps_entries" "trusted_issuers" {
  for_each = local.flattened_issuer_entries

  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.cross_cloud.id
  name               = each.key
  value              = each.value
}

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = local.bundle_root
  output_path = "${path.module}/.terraform/tmp/cross-cloud.zip"
}

resource "google_apigee_sharedflow" "cross_cloud" {
  org_id         = local.org_name
  name           = var.sharedflow_name
  config_bundle  = data.archive_file.bundle.output_path
  detect_md5hash = data.archive_file.bundle.output_md5
}

resource "google_apigee_sharedflow_deployment" "cross_cloud" {
  org_id          = local.org_name
  environment     = var.apigee_env_name
  sharedflow_id   = google_apigee_sharedflow.cross_cloud.name
  revision        = google_apigee_sharedflow.cross_cloud.latest_revision_id
  service_account = var.service_account

  depends_on = [
    google_apigee_environment_keyvaluemaps.cross_cloud,
  ]
}
