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
  value       = google_apigee_api.partner_agents.name
}

output "revision" {
  description = "Latest deployed revision ID of the partner-agents proxy."
  value       = google_apigee_api.partner_agents.latest_revision_id
}

output "allowed_agents" {
  description = "Agent slugs exposed to external partners from the service catalog."
  value       = local.allowed_agent_names
}

output "routes" {
  description = "Partner agent routes exposed for `make test-partner`."
  value = {
    for name, entry in local.partner_exposed_agents : name => {
      host      = "${name}.${var.apex_domain}"
      base_path = "/partner/v1/agents/${name}"
      probe     = coalesce(entry.probe, "query")
    }
  }
}
