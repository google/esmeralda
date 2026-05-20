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

/**
 * Copyright 2025 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

# ==============================================================================
# ORGANIZATION OUTPUTS
# ==============================================================================

output "org_id" {
  description = "Apigee organization ID"
  value       = module.apigee.org_id
}

output "org_name" {
  description = "Apigee organization name"
  value       = module.apigee.org_name
}

output "organization_id" {
  description = "Apigee organization ID (full resource path)"
  value       = try(module.apigee.organization_id, null)
}

output "tenant_project_id" {
  description = "Apigee tenant project ID (the actual project where Apigee infrastructure runs, used for PSC connections)"
  value       = local.apigee_tenant_project_id
}

output "service_agent_email" {
  description = "Apigee service agent email"
  value       = local.apigee_service_agent
}

# ==============================================================================
# ENVIRONMENT GROUP OUTPUTS
# ==============================================================================

output "envgroups" {
  description = "Map of environment group names to their details"
  value       = try(module.apigee.envgroups, {})
}

# ==============================================================================
# ENVIRONMENT OUTPUTS
# ==============================================================================

output "environments" {
  description = "Map of Apigee environment names to their details"
  value       = google_apigee_environment.environments
}

output "environment_ids" {
  description = "Map of environment names to their IDs"
  value = {
    for name, env in google_apigee_environment.environments :
    name => env.id
  }
}

# ==============================================================================
# INSTANCE OUTPUTS
# ==============================================================================

output "instances" {
  description = "Map of Apigee instance regions to their details"
  value       = try(module.apigee.instances, {})
}

output "endpoint" {
  description = "Apigee runtime endpoint"
  value       = try(module.apigee.endpoint, null)
}

# ==============================================================================
# ENDPOINT ATTACHMENT OUTPUTS
# ==============================================================================

output "endpoint_attachment_hosts" {
  description = "Map of endpoint attachment names to their PSC endpoint hosts/IPs"
  value       = try(module.apigee.endpoint_attachment_hosts, {})
}

# ==============================================================================
# SERVICE ACCOUNT OUTPUTS
# ==============================================================================

output "proxy_runtime_sa_email" {
  description = "Email of the Apigee proxy runtime service account"
  value       = var.create_service_accounts ? google_service_account.proxy_runtime[0].email : null
}

output "apim_operator_sa_email" {
  description = "Email of the APIM Operator GSA (for KSA annotation: iam.gke.io/gcp-service-account)"
  value       = var.create_apim_operator_iam ? google_service_account.apim_operator[0].email : null
}

output "apim_operator_sa_name" {
  description = "Full resource name of the APIM Operator GSA"
  value       = var.create_apim_operator_iam ? google_service_account.apim_operator[0].name : null
}

# ==============================================================================
# NORTHBOUND LOAD BALANCER OUTPUTS
# ==============================================================================

output "northbound_lb_ip" {
  description = "Internal IP address of the Apigee northbound load balancer"
  value       = var.northbound_lb != null ? google_compute_address.northbound_lb[0].address : null
}

output "northbound_forwarding_rule_id" {
  description = "ID of the Apigee northbound forwarding rule"
  value       = var.northbound_lb != null ? google_compute_forwarding_rule.northbound[0].id : null
}

# ==============================================================================
# SOUTHBOUND DNS PEERING OUTPUTS
# ==============================================================================

output "dns_peering_zones" {
  description = "Map of Apigee DNS peering zone IDs"
  value = merge(
    local.dns_first_zone_key != null ? {
      (local.dns_first_zone_key) = {
        id     = google_apigee_dns_zone.dns_peering_first[0].id
        domain = google_apigee_dns_zone.dns_peering_first[0].domain
      }
    } : {},
    {
      for k, v in google_apigee_dns_zone.dns_peering_remaining : k => {
        id     = v.id
        domain = v.domain
      }
    }
  )
}
