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
  org_full_id = "organizations/${local.org_name}"
  bundle_root = coalesce(var.bundle_source_dir, "${path.module}/../../../../../apps/apigee/sharedflows/usage-metering")

  data_collectors = {
    dc_llm_model = {
      type        = "STRING"
      description = "LLM model identifier extracted from agent or model response."
    }
    dc_prompt_tokens = {
      type        = "INTEGER"
      description = "Input/prompt token count for the interaction."
    }
    dc_candidates_tokens = {
      type        = "INTEGER"
      description = "Output/candidates token count for the interaction."
    }
    dc_total_tokens = {
      type        = "INTEGER"
      description = "Total token count (prompt + candidates) for the interaction."
    }
    dc_consumer_app = {
      type        = "STRING"
      description = "Verified Apigee consumer application name."
    }
  }
}

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = local.bundle_root
  output_path = "${path.module}/.terraform/tmp/usage-metering.zip"
}

resource "google_apigee_data_collector" "llm_metrics" {
  for_each = local.data_collectors

  org_id            = local.org_full_id
  data_collector_id = each.key
  type              = each.value.type
  description       = each.value.description
}

resource "google_apigee_sharedflow" "usage_metering" {
  org_id         = local.org_name
  name           = var.sharedflow_name
  config_bundle  = data.archive_file.bundle.output_path
  detect_md5hash = data.archive_file.bundle.output_md5
}

resource "google_apigee_sharedflow_deployment" "usage_metering" {
  org_id          = local.org_name
  environment     = var.apigee_env_name
  sharedflow_id   = google_apigee_sharedflow.usage_metering.name
  revision        = google_apigee_sharedflow.usage_metering.latest_revision_id
  service_account = var.service_account

  depends_on = [
    google_apigee_data_collector.llm_metrics,
  ]
}
