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
  type        = string
  description = "The Google Cloud Project ID"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The region to deploy the Reasoning Engine"
}

variable "staging_bucket_name" {
  type        = string
  description = "The GCS bucket name used to stage agent artifacts"
}

variable "agent_name" {
  type        = string
  description = "The display name of the agent reasoning engine"
}

variable "service_account" {
  type        = string
  description = "The service account email used by the reasoning engine"
}

variable "pickle_object_path" {
  type        = string
  description = "Local path to the serialized agent.pkl file"
}

variable "requirements_path" {
  type        = string
  description = "Local path to the requirements.txt file"
}

variable "dependencies_path" {
  type        = string
  description = "Local path to the packaged dependencies.tar.gz file"
}

variable "network_attachment" {
  type        = string
  default     = ""
  description = "Optional Private Service Connect Network Attachment ID"
}
