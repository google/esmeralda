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
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

# Inbound cross-cloud caller governance module (when governance.capabilities.cross_cloud = "apigee").
# Outbound multi-cloud egress from Esmeralda agents is governed natively by Agent Gateway + Agent Registry.
# This module manages the Apigee KeyValueMap entries for trusted external OIDC/JWT issuers (e.g. AWS IAM OIDC,
# Azure AD / Entra ID) that are validated and exchanged before forwarding to internal entrypoints.

resource "google_apigee_environment_keyvaluemaps" "cross_cloud" {
  env_id = var.apigee_env_id
  name   = "kvm-cross-cloud-issuers"
}

resource "google_apigee_environment_keyvaluemaps_entries" "trusted_issuers" {
  for_each = var.trusted_issuers

  env_keyvaluemap_id = google_apigee_environment_keyvaluemaps.cross_cloud.id
  name               = each.key
  value              = jsonencode(each.value)
}
