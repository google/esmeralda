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

variable "org_id" {
  description = "The Apigee organization resource ID in the format organizations/<org>."
  type        = string
}

variable "project_id" {
  description = "The GCP project ID where Secret Manager consumer API key secrets are stored."
  type        = string
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, prd)."
  type        = string
  default     = "dev"
}

variable "apigee_env_name" {
  description = "The Apigee environment name to bind API products to."
  type        = string
  default     = "esmeralda-dev"
}

variable "products" {
  description = "Catalog of Apigee API products defining proxy bindings, allowed paths, server-side MCP tool allowlists, and LLM token quota attributes."
  type = map(object({
    display_name        = string
    description         = optional(string, "Managed by Esmeralda Terraform")
    approval_type       = optional(string, "auto")
    proxies             = optional(list(string), [])
    allowed_paths       = optional(list(string), ["/", "/*"])
    allowed_mcp_tools   = optional(list(string), [])
    llm_quota_limit     = optional(number, 100000)
    llm_quota_interval  = optional(number, 1)
    llm_quota_time_unit = optional(string, "hour")
  }))
  default = {}

  validation {
    condition = alltrue([
      for p in values(var.products) : contains(["auto", "manual"], p.approval_type)
    ])
    error_message = "products[*].approval_type must be 'auto' or 'manual'."
  }

  validation {
    condition = alltrue([
      for p in values(var.products) : contains(["minute", "hour", "day", "month"], p.llm_quota_time_unit)
    ])
    error_message = "products[*].llm_quota_time_unit must be one of minute, hour, day, or month."
  }
}

variable "consumers" {
  description = "Map of partner or internal consumer applications, their subscribed API products, and owner_identity binding."
  type = map(object({
    display_name                     = string
    developer_email                  = string
    products                         = list(string)
    owner_identity                   = string
    callback_url                     = optional(string, "https://partner.esmeralda.internal/oauth/callback")
    secret_accessor_service_accounts = optional(list(string), [])
  }))
  default = {}
}

variable "secret_accessor_service_accounts" {
  description = "Service account emails granted roles/secretmanager.secretAccessor on all generated consumer API key secrets."
  type        = list(string)
  default     = []
}
