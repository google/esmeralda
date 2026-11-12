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
  description = "Apigee organization ID (organizations/<org> or <org>)."
  type        = string
}

variable "apigee_env_name" {
  description = "Apigee environment name where the llm-gateway proxy is deployed."
  type        = string
}

variable "apigee_env_id" {
  description = "Apigee environment resource ID (organizations/<org>/environments/<env>)."
  type        = string
}

variable "service_account" {
  description = "Service account the deployed proxy uses to mint its southbound Google access token for Vertex AI."
  type        = string
}

variable "proxy_name" {
  description = "Name of the Apigee API proxy for agent-to-model traffic."
  type        = string
  default     = "llm-gateway"
}

variable "bundle_source_dir" {
  description = "Optional override path to the llm-gateway bundle directory."
  type        = string
  default     = null
}

variable "gateway_host" {
  description = "Host agents call the gateway on (served by Apigee through the entry point). The caller ID token audience is https://<gateway_host>."
  type        = string
  default     = "apigee.esmeralda.internal"

  validation {
    condition     = can(regex("^[a-z0-9.-]+$", var.gateway_host))
    error_message = "gateway_host must be a bare hostname (no scheme, port or path)."
  }
}

variable "allowed_callers" {
  description = "Verified caller principals allowed to call models: the email (or subject) of the Google ID token each agent sends. Empty denies every caller."
  type        = list(string)
  default     = []
}

variable "model_projects" {
  description = "Projects whose Vertex AI models agents may call through the gateway (the project in the request path). The proxy service account gets roles/aiplatform.user on each."
  type        = list(string)
  default     = []
}

variable "allowed_locations" {
  description = "Vertex AI locations agents may call. The southbound host is derived from the location (global -> aiplatform.googleapis.com)."
  type        = list(string)
  default     = ["us-central1", "global"]

  validation {
    condition     = alltrue([for location in var.allowed_locations : can(regex("^[a-z0-9-]+$", location))])
    error_message = "allowed_locations entries must be Vertex AI location IDs (lowercase letters, digits and dashes)."
  }
}

variable "allowed_models" {
  description = "Publisher models agents may call: exact IDs, `prefix*` patterns or `*`."
  type        = list(string)
  default     = ["gemini-*"]
}

variable "quota_tokens_per_interval" {
  description = "LLMTokenQuota per verified caller: tokens (input + output) allowed per quota_time_unit."
  type        = number
  default     = 1000000

  validation {
    condition     = var.quota_tokens_per_interval > 0
    error_message = "quota_tokens_per_interval must be positive."
  }
}

variable "quota_time_unit" {
  description = "LLMTokenQuota interval unit: minute, hour, day or month."
  type        = string
  default     = "minute"

  validation {
    condition     = contains(["minute", "hour", "day", "month"], var.quota_time_unit)
    error_message = "quota_time_unit must be one of: minute, hour, day, month."
  }
}

variable "grant_vertex_access" {
  description = "Grant roles/aiplatform.user on model_projects to the proxy service account (disable when IAM is managed elsewhere)."
  type        = bool
  default     = true
}
