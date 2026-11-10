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

variable "org_id" {
  description = "Apigee organization ID (organizations/<org> or <org>)."
  type        = string
}

variable "apigee_env_name" {
  description = "Apigee environment name where the partner-mcp proxy is deployed."
  type        = string
}

variable "apigee_env_id" {
  description = "Apigee environment resource ID (organizations/<org>/environments/<env>)."
  type        = string
}

variable "service_account" {
  description = "Service account email used by the deployed Apigee proxy to mint upstream GoogleIDToken credentials."
  type        = string
}

variable "proxy_name" {
  description = "Name of the Apigee API proxy for partner MCP access."
  type        = string
  default     = "partner-mcp"
}

variable "apex_domain" {
  description = "Private internal domain used for entrypoint host routing."
  type        = string
  default     = "esmeralda.internal"
}

variable "bundle_source_dir" {
  description = "Optional override path to the partner-mcp bundle directory."
  type        = string
  default     = null
}

# --- southbound path to the entry point -------------------------------------------------------

variable "southbound_enabled" {
  description = "Publish the internal entry point to Apigee through PSC (NAT subnet, service attachment, endpoint attachment, internal CA truststore) and point the MCP target server at it."
  type        = bool
  default     = false
}

variable "project_id" {
  description = "Project of the entry point forwarding rule (the gateway project); hosts the PSC service attachment."
  type        = string
  default     = null
}

variable "region" {
  description = "Region of the entry point, the PSC service attachment and the Apigee instance."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment (dev, prd): resource name suffix."
  type        = string
  default     = "dev"
}

variable "network_project_id" {
  description = "Shared VPC host project that holds the PSC NAT subnet."
  type        = string
  default     = null
}

variable "network_id" {
  description = "Shared VPC network ID for the PSC NAT subnet."
  type        = string
  default     = null
}

variable "psc_nat_cidr" {
  description = "CIDR of the PSC NAT subnet (purpose PRIVATE_SERVICE_CONNECT) used by the entry point service attachment."
  type        = string
  default     = "10.13.0.0/24"
}

variable "entrypoint_forwarding_rule_name" {
  description = "Name of the entry point forwarding rule (defaults to ilb-esmeralda-rule-<environment>)."
  type        = string
  default     = null
}

variable "southbound_host_label" {
  description = "Label of the southbound target server name (<label>.<apex_domain>, covered by the entry point certificate). A private zone of the Shared VPC, DNS-peered to the Apigee org, resolves it to the endpoint attachment IP; it must not be a catalog host."
  type        = string
  default     = "apigee-southbound"
}

variable "internal_ca_cert_pem" {
  description = "PEM of the internal Root CA that signs the entry point certificate (layer 3 internal_ca_cert_pem)."
  type        = string
  default     = ""

  validation {
    condition     = !var.southbound_enabled || length(trimspace(var.internal_ca_cert_pem)) > 0
    error_message = "internal_ca_cert_pem is required when southbound_enabled is true."
  }
}

variable "mcp_target_server_name" {
  description = "Apigee target server used by the partner-mcp TargetEndpoint (must match apiproxy/targets/default.xml)."
  type        = string
  default     = "ts-esmeralda-mcp"
}

variable "mcp_target_host" {
  description = "Target server host when southbound_enabled is false (bring-your-own southbound path)."
  type        = string
  default     = null
}

variable "mcp_target_trust_store" {
  description = "Truststore (keystore name or ref://) of the target server when southbound_enabled is false."
  type        = string
  default     = null
}

variable "catalog" {
  description = "Service catalog entries; entries with expose_to_partners = true and kind = 'mcp' populate the KVM allowlist."
  type = map(object({
    kind               = optional(string, "mcp")
    expose_to_partners = optional(bool, false)
    probe              = optional(string, "mcp")
  }))
  default = {
    corporate-email = {
      kind               = "mcp"
      expose_to_partners = true
      probe              = "mcp"
    }
    income-verification = {
      kind               = "mcp"
      expose_to_partners = true
      probe              = "mcp"
    }
  }
}
