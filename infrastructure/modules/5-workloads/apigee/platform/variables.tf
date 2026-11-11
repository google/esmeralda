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
  description = "The GCP project ID hosting the Apigee organization and gateway ingress resources."
  type        = string
}

variable "region" {
  description = "The GCP region for the Apigee runtime instance, PSC NEG, and backend service."
  type        = string
}

variable "analytics_region" {
  description = "The GCP region for Apigee analytics data storage (defaults to var.region when null)."
  type        = string
  default     = null
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, prd)."
  type        = string
  default     = "dev"
}

variable "vpc_id" {
  description = "The self-link or resource ID of the Shared VPC network used by the northbound PSC NEG."
  type        = string
}

variable "subnet_id" {
  description = "The self-link or resource ID of the subnetwork used by the northbound PSC NEG."
  type        = string
}

variable "byo_apigee_org" {
  description = "When true, skips provisioning the Apigee organization and runtime instance and attaches to an existing Apigee organization and instance service attachment."
  type        = bool
  default     = false
}

variable "existing_org_id" {
  description = "Existing Apigee organization resource ID (organizations/<org>) when byo_apigee_org = true."
  type        = string
  default     = null

  validation {
    condition     = !var.byo_apigee_org || (var.existing_org_id != null && var.existing_org_id != "")
    error_message = "existing_org_id must be provided when byo_apigee_org is true."
  }
}

variable "existing_instance_service_attachment" {
  description = "Existing Apigee instance PSC service attachment URI when byo_apigee_org = true."
  type        = string
  default     = null

  validation {
    condition     = !var.byo_apigee_org || (var.existing_instance_service_attachment != null && var.existing_instance_service_attachment != "")
    error_message = "existing_instance_service_attachment must be provided when byo_apigee_org is true."
  }
}

variable "billing_type" {
  description = "Apigee organization billing type: PAYG (default), EVALUATION, or SUBSCRIPTION."
  type        = string
  default     = "PAYG"

  validation {
    condition     = contains(["PAYG", "EVALUATION", "SUBSCRIPTION"], var.billing_type)
    error_message = "billing_type must be one of PAYG, EVALUATION, or SUBSCRIPTION."
  }
}

variable "apigee_env_name" {
  description = "Name of the Apigee environment provisioned for Esmeralda proxy deployments."
  type        = string
  default     = "esmeralda-dev"
}

variable "environment_type" {
  description = "Apigee PAYG environment tier: BASE, INTERMEDIATE, or COMPREHENSIVE."
  type        = string
  default     = "BASE"

  validation {
    condition     = contains(["BASE", "INTERMEDIATE", "COMPREHENSIVE"], var.environment_type)
    error_message = "environment_type must be one of BASE, INTERMEDIATE, or COMPREHENSIVE."
  }
}

variable "enable_analytics" {
  description = "Whether to enable the Apigee Analytics add-on on the environment (PAYG, per-call cost). Feeds the console Analytics dashboards and the stats API."
  type        = bool
  default     = false
}

variable "enable_advanced_api_ops" {
  description = "Whether to enable the org-level Advanced API Ops add-on (separate paid add-on, not needed for Analytics)."
  type        = bool
  default     = false
}

variable "enable_cmek" {
  description = "Whether to provision and configure Cloud KMS CMEK keys for the Apigee organization runtime database and instance disk."
  type        = bool
  default     = false
}

variable "envgroup_name" {
  description = "Name of the Apigee environment group."
  type        = string
  default     = "esmeralda-envgroup"
}

variable "envgroup_hostnames" {
  description = "Hostnames bound to the Apigee environment group."
  type        = list(string)
  default     = ["partner.esmeralda.internal", "apigee.esmeralda.internal"]
}

variable "consumer_accept_list" {
  description = "List of GCP project IDs or numbers allowed to connect to the Apigee instance PSC service attachment (defaults to [var.project_id] when empty)."
  type        = list(string)
  default     = []
}

variable "entrypoint_service_attachment" {
  description = "Optional PSC service attachment URI for the southbound Esmeralda internal entrypoint ALB. When set, provisions a google_apigee_endpoint_attachment and ts-esmeralda-entrypoint target server."
  type        = string
  default     = null
}

variable "routes" {
  description = "Map of logical route names to host and probe definitions exposed through the entrypoint gateway contract."
  type = map(object({
    host  = string
    probe = optional(string, "none")
  }))
  default = {}
}
