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

variable "project_id" {
  description = "GCP project ID where the esmeralda-ui Cloud Run service is deployed."
  type        = string
}

variable "governance_project_id" {
  description = "Central governance project ID holding the runtime catalog secret, BigQuery views, and Cloud Logging."
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud Run service."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (e.g. dev, prd)."
  type        = string
}

variable "network_id" {
  description = "Shared VPC network ID for Direct VPC egress to *.esmeralda.internal."
  type        = string
}

variable "subnet_id" {
  description = "Shared VPC subnet ID for Direct VPC egress."
  type        = string
}

variable "container_image" {
  description = "Container image URI (pinned by digest in prd)."
  type        = string
}

variable "entrypoint_domain" {
  description = "Internal entrypoint domain (e.g. esmeralda.internal)."
  type        = string
  default     = "esmeralda.internal"
}

variable "runtime_catalog_secret_id" {
  description = "Secret Manager secret ID in governance_project_id holding the bound runtime catalog JSON."
  type        = string
  default     = ""
}

variable "root_ca_secret_id" {
  description = "Optional Secret Manager secret ID holding the internal Root CA PEM bundle for NODE_EXTRA_CA_CERTS."
  type        = string
  default     = ""
}

variable "iap_enabled" {
  description = "Enable native Direct Cloud Run IAP on the .run.app URL (no App Engine or External ALB required)."
  type        = bool
  default     = true
}

variable "iap_accessors" {
  description = "IAM principals granted roles/iap.httpsResourceAccessor on esmeralda-ui (e.g. ['group:esmeralda-viewers@example.com'])."
  type        = list(string)
  default     = []
}

variable "invoker_members" {
  description = "Additional IAM principals granted roles/run.invoker (e.g. for `esmeralda ui` / `gcloud run services proxy`)."
  type        = list(string)
  default     = []
}

variable "identity_platform" {
  description = "Optional Identity Platform (GCIP) configuration on Cloud Run IAP for sharing with external customer emails without triggering Domain Restricted Sharing (iam.allowedPolicyMemberDomains)."
  type = object({
    enabled        = optional(bool, false)
    tenant_ids     = optional(list(string), [])
    login_page_uri = optional(string, "")
  })
  default = {
    enabled        = false
    tenant_ids     = []
    login_page_uri = ""
  }
}

variable "allow_persona_override" {
  description = "Allow switching synthetic personas in the UI for policy verification scenarios (recommended true in dev, false in prd)."
  type        = bool
  default     = false
}
