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

output "backend_service_id" {
  description = "ID of the regional INTERNAL_MANAGED backend service fronting the Apigee PSC NEG (entrypoint gateway contract)."
  value       = google_compute_region_backend_service.apigee.id
}

output "routes" {
  description = "Hosts served by Apigee and how `make test-gateway` probes them (entrypoint gateway contract)."
  value       = var.routes
}

output "org_id" {
  description = "The Apigee organization resource ID (organizations/<org>)."
  value       = local.org_id
}

output "apigee_env_name" {
  description = "The provisioned Apigee environment name."
  value       = google_apigee_environment.env.name
}

output "apigee_env_id" {
  description = "The provisioned Apigee environment resource ID."
  value       = google_apigee_environment.env.id
}

output "envgroup_id" {
  description = "The provisioned Apigee environment group resource ID."
  value       = google_apigee_envgroup.envgroup.id
}

output "instance_service_attachment" {
  description = "PSC service attachment URI exposed by the Apigee runtime instance."
  value       = local.instance_service_attachment
}

output "proxy_runtime_sa_email" {
  description = "Email of the service account used by Apigee proxies for upstream Google authentication."
  value       = google_service_account.apigee_proxy_runtime.email
}

output "entrypoint_target_server_name" {
  description = "Name of the southbound Apigee target server pointing to the Esmeralda entrypoint or regional Vertex AI endpoint."
  value       = google_apigee_target_server.entrypoint.name
}

output "entrypoint_endpoint_attachment_host" {
  description = "Internal IP/host allocated by the southbound Apigee endpoint attachment (null when entrypoint_service_attachment is unset)."
  value       = var.entrypoint_service_attachment != null ? google_apigee_endpoint_attachment.entrypoint[0].host : null
}
