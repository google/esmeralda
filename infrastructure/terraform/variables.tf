# Copyright 2025 Google LLC
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

variable "create_project" {
  type        = bool
  description = "Set to true to create a new GCP project, false to use an existing one."
  default     = true
}

variable "project_id" {
  type        = string
  description = "The ID of the GCP project to use or create."
}

variable "region" {
  type        = string
  description = "The GCP region to deploy resources in."
}

variable "agent_name" {
  type        = string
  description = "The name for the Vertex AI Agent Engine."
  default     = "base-adk-agent"
}

variable "org_id" {
  type        = string
  description = "GCP organization ID. Required if create_project is true and folder_id is not set."
  default     = null
}

variable "folder_id" {
  type        = string
  description = "GCP folder ID. If set, project will be created under this folder instead of org_id."
  default     = null
}

variable "billing_account" {
  type        = string
  description = "GCP billing account ID. Required if create_project is true."
  default     = null
}


