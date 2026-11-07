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
  description = "Apigee environment name where the usage-metering shared flow is deployed."
  type        = string
}

variable "service_account" {
  description = "Service account email used by the deployed Apigee shared flow."
  type        = string
}

variable "sharedflow_name" {
  description = "Name of the Apigee shared flow for LLM usage metering and Cloud Logging."
  type        = string
  default     = "usage-metering"
}

variable "bundle_source_dir" {
  description = "Optional override path to the usage-metering sharedflow bundle directory."
  type        = string
  default     = null
}
