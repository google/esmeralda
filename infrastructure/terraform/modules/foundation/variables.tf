# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

variable "project_id" {
  description = "The ID of the project to create."
  type        = string
}

variable "random_suffix" {
  description = "A random suffix to append to the project ID."
  type        = string
}

variable "org_id" {
  description = "The organization ID to create the project in."
  type        = string
  default     = null
}

variable "folder_id" {
  description = "The folder ID to create the project in."
  type        = string
  default     = null
}

variable "billing_account" {
  description = "The billing account to link to the project."
  type        = string
}

variable "apis_to_enable" {
  description = "A list of APIs to enable on the project."
  type        = list(string)
  default     = []
}

variable "enable_psc_interface" {
  description = "Whether to enable IAM bindings for PSC interface."
  type        = bool
  default     = false
}
