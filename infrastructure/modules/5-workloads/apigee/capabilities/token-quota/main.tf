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
  bundle_root = coalesce(var.bundle_source_dir, "${path.module}/../../../../../apps/apigee/sharedflows/token-quota")
}

data "archive_file" "bundle" {
  type        = "zip"
  source_dir  = local.bundle_root
  output_path = "${path.module}/.terraform/tmp/token-quota.zip"
}

resource "google_apigee_sharedflow" "token_quota" {
  org_id         = local.org_name
  name           = var.sharedflow_name
  config_bundle  = data.archive_file.bundle.output_path
  detect_md5hash = data.archive_file.bundle.output_md5
}

resource "google_apigee_sharedflow_deployment" "token_quota" {
  org_id          = local.org_name
  environment     = var.apigee_env_name
  sharedflow_id   = google_apigee_sharedflow.token_quota.name
  revision        = google_apigee_sharedflow.token_quota.latest_revision_id
  service_account = var.service_account
}
