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
  bundle_root = coalesce(var.bundle_source_dir, "${path.module}/../../../../../apps/apigee/proxies/llm-gateway")
  audience    = "https://${var.gateway_host}"

  # kvm-llm-gateway, read by KVM-Get-LLM-Gateway-Config. Apigee rejects empty KVM values, so an
  # empty allowlist is written as "none" (matches no caller, project, location or model: fail closed).
  kvm_entries = {
    audience          = local.audience
    allowed_callers   = length(var.allowed_callers) > 0 ? join(",", sort(distinct(var.allowed_callers))) : "none"
    allowed_projects  = length(var.model_projects) > 0 ? join(",", sort(distinct(var.model_projects))) : "none"
    allowed_locations = length(var.allowed_locations) > 0 ? join(",", sort(distinct(var.allowed_locations))) : "none"
    allowed_models    = length(var.allowed_models) > 0 ? join(",", var.allowed_models) : "none"
    quota_limit       = tostring(var.quota_tokens_per_interval)
    quota_timeunit    = var.quota_time_unit
  }
}

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = local.bundle_root
  output_path = "${path.module}/.terraform/tmp/llm-gateway.zip"
}

resource "google_apigee_environment_keyvaluemaps" "llm_gateway" {
  env_id = var.apigee_env_id
  name   = "kvm-llm-gateway"
}

resource "google_apigee_environment_keyvaluemaps_entries" "config" {
  for_each = local.kvm_entries

  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.llm_gateway.id
  name               = each.key
  value              = each.value
}

# Southbound: the proxy calls Vertex AI with its own access token (GoogleAccessToken), so its
# service account needs roles/aiplatform.user on every project agents send model calls for.
resource "google_project_iam_member" "proxy_vertex_user" {
  for_each = var.grant_vertex_access ? toset(var.model_projects) : toset([])

  project = each.value
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${var.service_account}"
}

resource "google_apigee_api" "llm_gateway" {
  org_id         = local.org_name
  name           = var.proxy_name
  config_bundle  = data.archive_file.bundle.output_path
  detect_md5hash = data.archive_file.bundle.output_md5
}

resource "google_apigee_environment_api_revision_deployment" "llm_gateway" {
  org_id          = local.org_name
  environment     = var.apigee_env_name
  api             = google_apigee_api.llm_gateway.name
  revision        = tonumber(google_apigee_api.llm_gateway.latest_revision_id)
  override        = true
  service_account = var.service_account

  depends_on = [
    google_apigee_environment_keyvaluemaps_entries.config,
    google_project_iam_member.proxy_vertex_user,
  ]
}
