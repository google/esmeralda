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

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "a2a-agent"
}

variable "tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "vpc_id" {
  description = "VPC network ID for private IP and service networking peering"
  type        = string
}

variable "database_name" {
  description = "Database name for A2A task storage"
  type        = string
  default     = "a2a_tasks"
}

variable "agent_service_account" {
  description = "Service account email used by Agent Engine"
  type        = string
}

variable "enable_iam_user" {
  description = "Enable creation of the CLOUD_IAM_SERVICE_ACCOUNT user and database grants"
  type        = bool
  default     = false
}
