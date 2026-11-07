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
  description = "Apigee environment name where the partner-agents proxy is deployed."
  type        = string
}

variable "apigee_env_id" {
  description = "Apigee environment resource ID (organizations/<org>/environments/<env>)."
  type        = string
}

variable "service_account" {
  description = "Service account email used by the deployed Apigee proxy to mint upstream Google credentials."
  type        = string
}

variable "proxy_name" {
  description = "Name of the Apigee API proxy for partner agent access."
  type        = string
  default     = "partner-agents"
}

variable "apex_domain" {
  description = "Private internal domain used for entrypoint host routing."
  type        = string
  default     = "esmeralda.internal"
}

variable "bundle_source_dir" {
  description = "Optional override path to the partner-agents bundle directory."
  type        = string
  default     = null
}

variable "catalog" {
  description = "Service catalog entries; entries with expose_to_partners = true and kind = 'agent' populate the KVM allowlist."
  type = map(object({
    kind               = optional(string, "agent")
    expose_to_partners = optional(bool, false)
    probe              = optional(string, "query")
  }))
  default = {
    cx-mortgage-orchestrator = {
      kind               = "agent"
      expose_to_partners = true
      probe              = "query"
    }
  }
}
