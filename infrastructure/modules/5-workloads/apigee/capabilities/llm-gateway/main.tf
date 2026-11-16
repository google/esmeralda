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

  # Agent Identity ID tokens are signed by the agent's trust domain on Security Token Service:
  #   principal://agents.global.org-<n>.system.id.goog/...  -> iss https://sts.googleapis.com/v1/organizations/<n>/locations/global/workloadIdentityPools/agents.global.org-<n>.system.id.goog
  #   principal://agents.global.proj-<n>.system.id.goog/... -> iss https://sts.googleapis.com/v1/projects/<n>/locations/global/workloadIdentityPools/agents.global.proj-<n>.system.id.goog
  # (JWKS: <iss>/openid/jwks, from the issuer's /.well-known/openid-configuration).
  agent_trust_domains = {
    for name, principal in var.agent_principals :
    name => regex("^principal://(agents\\.global\\.(org|proj)-([0-9]+)\\.system\\.id\\.goog)/", principal)
  }
  agent_issuers = distinct([
    for name, td in local.agent_trust_domains :
    "https://sts.googleapis.com/v1/${td[1] == "org" ? "organizations" : "projects"}/${td[2]}/locations/global/workloadIdentityPools/${td[0]}"
  ])
  callers = distinct(concat(values(var.agent_principals), var.allowed_callers))

  # The project in the request path is the agent's GOOGLE_CLOUD_PROJECT, which Agent Runtime sets to
  # the project NUMBER (elsewhere it's usually the ID): allow both forms of each model project.
  model_project_refs = distinct(concat(var.model_projects, [for p in data.google_project.model : p.number]))

  # kvm-llm-gateway, read by KVM-Get-LLM-Gateway-Config. Apigee rejects empty KVM values, so an
  # empty allowlist is written as "none" (matches no caller, issuer, project, location or model:
  # fail closed).
  kvm_entries = {
    audience          = local.audience
    allowed_callers   = length(local.callers) > 0 ? join(",", sort(local.callers)) : "none"
    agent_issuers     = length(local.agent_issuers) > 0 ? join(",", sort(local.agent_issuers)) : "none"
    caller_workloads  = length(var.agent_principals) > 0 ? join(",", sort([for name, principal in var.agent_principals : "${principal}=${name}"])) : "none"
    allowed_projects  = length(local.model_project_refs) > 0 ? join(",", sort(local.model_project_refs)) : "none"
    allowed_locations = length(var.allowed_locations) > 0 ? join(",", sort(distinct(var.allowed_locations))) : "none"
    allowed_models    = length(var.allowed_models) > 0 ? join(",", var.allowed_models) : "none"
    quota_limit       = tostring(var.quota_tokens_per_interval)
    quota_timeunit    = var.quota_time_unit
  }
}

data "google_project" "model" {
  for_each   = toset(var.model_projects)
  project_id = each.value
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
