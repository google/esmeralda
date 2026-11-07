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

# --- INPUT VARIABLES CONTRACT ---
variable "project_id" {
  description = "The GCP project ID allocated for gateway ingress (prj-esmeralda-gateway)"
  type        = string
}

variable "region" {
  description = "The region where gateway workloads are deployed"
  type        = string
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the gateway/backend subnetwork"
  type        = string
}

variable "agent_endpoints" {
  description = "A map of logical agent names to their Agent Runtime endpoint configuration"
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
  }))
  default = {}
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "environment_type" {
  description = "Apigee PAYG environment tier: BASE, INTERMEDIATE, or COMPREHENSIVE"
  type        = string
  default     = "BASE"
}

variable "byo_apigee_org" {
  description = "When true, attaches to an existing Apigee organization and instance service attachment"
  type        = bool
  default     = false
}

variable "existing_org_id" {
  description = "Existing Apigee organization resource ID when byo_apigee_org = true"
  type        = string
  default     = null
}

variable "existing_instance_service_attachment" {
  description = "Existing Apigee instance PSC service attachment URI when byo_apigee_org = true"
  type        = string
  default     = null
}
