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
  bundle_root = coalesce(var.bundle_source_dir, "${path.module}/../../../../../apps/apigee/proxies/partner-agents")

  partner_exposed_agents = {
    for name, entry in var.catalog : name => entry
    if try(entry.expose_to_partners, false) && try(entry.kind, "agent") == "agent"
  }

  allowed_agent_names = sort(keys(local.partner_exposed_agents))
}

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = local.bundle_root
  output_path = "${path.module}/.terraform/tmp/partner-agents.zip"
}

resource "google_apigee_environment_keyvaluemaps" "partner_agents" {
  env_id = var.apigee_env_id
  name   = "kvm-partner-agents"
}

resource "google_apigee_environment_keyvaluemaps_entries" "allowed_agents" {
  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.partner_agents.id
  name               = "allowed_agents"
  value              = length(local.allowed_agent_names) > 0 ? join(",", local.allowed_agent_names) : "cx-mortgage-orchestrator"
}

resource "google_apigee_environment_keyvaluemaps_entries" "orchestrator_engine_id" {
  count              = var.orchestrator_engine_id != "" ? 1 : 0
  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.partner_agents.id
  name               = "orchestrator_engine_id"
  value              = var.orchestrator_engine_id
}

resource "google_project_iam_member" "proxy_sa_aiplatform_user" {
  count   = var.orchestrator_engine_id != "" && length(split("/", var.orchestrator_engine_id)) >= 2 ? 1 : 0
  project = split("/", var.orchestrator_engine_id)[1]
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${var.service_account}"
}

resource "google_apigee_api" "partner_agents" {
  org_id         = local.org_name
  name           = var.proxy_name
  config_bundle  = data.archive_file.bundle.output_path
  detect_md5hash = data.archive_file.bundle.output_md5
}

resource "google_apigee_environment_api_revision_deployment" "partner_agents" {
  org_id          = local.org_name
  environment     = var.apigee_env_name
  api             = google_apigee_api.partner_agents.name
  revision        = tonumber(google_apigee_api.partner_agents.latest_revision_id)
  override        = true
  service_account = var.service_account

  depends_on = [
    google_apigee_environment_keyvaluemaps_entries.allowed_agents,
    google_apigee_environment_keyvaluemaps_entries.orchestrator_engine_id,
    google_project_iam_member.proxy_sa_aiplatform_user,
  ]
}
