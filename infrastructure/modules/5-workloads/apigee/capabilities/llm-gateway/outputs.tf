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
  value       = google_apigee_api.llm_gateway.name
}

output "revision" {
  description = "Latest deployed revision ID of the llm-gateway proxy."
  value       = google_apigee_api.llm_gateway.latest_revision_id
}

output "base_url" {
  description = "Gemini base URL agents use (ESMERALDA_MODEL_GATEWAY_URL)."
  value       = "${local.audience}/llm"
}

output "audience" {
  description = "Audience of the ID token agents send to the gateway."
  value       = local.audience
}

output "allowed_callers" {
  description = "Verified caller principals allowed through the gateway (agent principals and other callers)."
  value       = sort(local.callers)
}

output "agent_issuers" {
  description = "Agent Identity trust domain issuers whose ID tokens the gateway verifies."
  value       = sort(local.agent_issuers)
}
