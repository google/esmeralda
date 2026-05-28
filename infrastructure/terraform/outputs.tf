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

output "project_id" {
  description = "The ID of the project."
  value       = local.project_id
}

output "random_suffix" {
  description = "The random suffix added to the project ID (if a new project was created)."
  value       = local.random_suffix
}

output "cloudbuild_sa_email" {
  description = "The email of the Cloud Build service account."
  value       = module.ci_cd.cloudbuild_sa_email
}

output "region" {
  description = "The GCP region."
  value       = var.region
}

output "repo_name" {
  description = "The name of the Artifact Registry repository."
  value       = module.ci_cd.agent_repository_url
}

output "agent_name" {
  description = "The name for the Vertex AI Agent Engine."
  value       = var.agent_name
}

output "agent_logs_offload_bucket_name" {
  description = "The name of the GCS bucket for agent logs."
  value       = module.data_and_logging.bucket_names["agent-logs-offload"]
}

output "vpc_name" {
  description = "The name of the VPC."
  value       = module.networking.network_name
}

output "psc_interface_network_attachment_id" {
  description = "The ID of the PSC interface network attachment."
  value       = module.networking.psc_interface_network_attachment_id
}

output "cloud_sql_instance_connection_name" {
  description = "Cloud SQL instance connection name (project:region:instance)."
  value       = module.cloud_sql.instance_connection_name
}
