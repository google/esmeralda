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

output "proxy_name" {
  description = "Deployed Apigee proxy name."
  value       = google_apigee_api.partner_mcp.name
}

output "revision" {
  description = "Latest deployed revision ID of the partner-mcp proxy."
  value       = google_apigee_api.partner_mcp.latest_revision_id
}

output "allowed_mcp_services" {
  description = "MCP service slugs exposed to external partners from the service catalog."
  value       = local.allowed_mcp_services
}

output "routes" {
  description = "Partner MCP routes exposed for `make test-partner`."
  value = {
    for name, entry in local.partner_exposed_mcp : name => {
      host      = "${name}.${var.apex_domain}"
      base_path = "/partner/v1/mcp/${name}/mcp"
      probe     = coalesce(entry.probe, "mcp")
    }
  }
}

output "mcp_target_server" {
  description = "Apigee target server of the partner-mcp TargetEndpoint and the host it reaches."
  value = {
    name = google_apigee_target_server.mcp.name
    host = google_apigee_target_server.mcp.host
  }
}

output "entrypoint_service_attachment_id" {
  description = "PSC service attachment publishing the entry point to Apigee (null when southbound_enabled is false)."
  value       = try(google_compute_service_attachment.entrypoint[0].id, null)
}
