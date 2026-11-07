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

# --- GATEWAY CONTRACT ---
# Delegates to modules/5-workloads/apigee/platform.

output "backend_service_id" {
  description = "ID of the regional INTERNAL_MANAGED backend service fronting Apigee"
  value       = module.platform.backend_service_id
}

output "routes" {
  description = "Hosts this gateway serves and how `make test-gateway` probes them"
  value       = module.platform.routes
}

output "org_id" {
  description = "The Apigee organization resource ID"
  value       = module.platform.org_id
}

output "proxy_runtime_sa_email" {
  description = "Email of the service account used by Apigee proxies for upstream authentication"
  value       = module.platform.proxy_runtime_sa_email
}
