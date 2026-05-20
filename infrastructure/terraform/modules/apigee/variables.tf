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
# REQUIRED VARIABLES
# ==============================================================================

variable "project_id" {
  description = "The GCP project ID"
  type        = string
}

variable "project_number" {
  description = "The GCP project number (required for Apigee service agent IAM bindings)"
  type        = string
}

variable "region" {
  description = "The GCP region for Apigee resources"
  type        = string
}

# ==============================================================================
# ORGANIZATION CONFIGURATION
# ==============================================================================

variable "organization" {
  description = <<-EOT
    Apigee organization configuration.

    Attributes:
    - display_name: Display name for the organization
    - description: Description for the organization
    - billing_type: PAYG or SUBSCRIPTION
    - analytics_region: Analytics region (defaults to var.region)
    - runtime_type: CLOUD or HYBRID
    - disable_vpc_peering: Set to true for Non-VPC Peering/PSC mode (recommended)
    - database_encryption_key: Optional KMS key for database encryption
  EOT
  type = object({
    display_name            = optional(string, "Apigee Organization")
    description             = optional(string, "Apigee Organization for API Management")
    billing_type            = optional(string, "PAYG")
    analytics_region        = optional(string)
    runtime_type            = optional(string, "CLOUD")
    disable_vpc_peering     = optional(bool, true)
    database_encryption_key = optional(string)
  })
  default = {}

  validation {
    condition     = contains(["PAYG", "SUBSCRIPTION"], var.organization.billing_type)
    error_message = "organization.billing_type must be either PAYG or SUBSCRIPTION"
  }

  validation {
    condition     = contains(["CLOUD", "HYBRID"], var.organization.runtime_type)
    error_message = "organization.runtime_type must be either CLOUD or HYBRID"
  }
}

# ==============================================================================
# VPC CONFIGURATION
# ==============================================================================

variable "vpc_id" {
  description = "VPC network ID for Apigee (only used when disable_vpc_peering is false)"
  type        = string
  default     = null
}

# ==============================================================================
# ENVIRONMENT GROUPS
# ==============================================================================

variable "envgroups" {
  description = <<-EOT
    Map of environment group names to hostnames.

    Example:
      envgroups = {
        prod = ["api.example.com", "api.prod.example.com"]
        dev  = ["api.dev.example.com"]
      }
  EOT
  type        = map(list(string))
  default     = {}
}

# ==============================================================================
# ENVIRONMENTS
# ==============================================================================

variable "environments" {
  description = <<-EOT
    Map of Apigee environments.

    Each environment supports:
    - display_name: Display name for the environment
    - description: Description for the environment
    - envgroups: List of environment groups to attach
    - type: INTERMEDIATE (required for APIM operator) or COMPREHENSIVE
    - node_config: Optional scaling configuration
    - properties: Optional environment properties (e.g., apigee-service-extension-enabled)

    Example:
      environments = {
        apis-prod = {
          display_name = "APIs Production"
          description  = "Production environment"
          envgroups    = ["prod"]
          type         = "INTERMEDIATE"
          properties   = {
            "apigee-service-extension-enabled" = "true"
          }
        }
      }
  EOT
  type = map(object({
    display_name = string
    description  = optional(string)
    envgroups    = list(string)
    type         = optional(string, "INTERMEDIATE")
    node_config = optional(object({
      min_node_count = optional(number)
      max_node_count = optional(number)
    }))
    properties = optional(map(string))
  }))
  default = {}

  validation {
    condition = alltrue([
      for env in var.environments :
      contains(["INTERMEDIATE", "COMPREHENSIVE"], coalesce(env.type, "INTERMEDIATE"))
    ])
    error_message = "environment.type must be INTERMEDIATE or COMPREHENSIVE"
  }
}

# ==============================================================================
# INSTANCES
# ==============================================================================

variable "instances" {
  description = <<-EOT
    Map of Apigee instances by region.

    For Non-VPC Peering mode (disable_vpc_peering = true), runtime_ip_cidr_range
    and troubleshooting_ip_cidr_range are NOT required.

    Example:
      instances = {
        us-east4 = {
          environments = ["apis-prod"]
          consumer_accept_list = ["projects/my-project"]
        }
      }
  EOT
  type = map(object({
    environments                  = list(string)
    runtime_ip_cidr_range         = optional(string)
    troubleshooting_ip_cidr_range = optional(string)
    consumer_accept_list          = optional(list(string))
    disk_encryption_key           = optional(string)
  }))
  default = {}
}

# ==============================================================================
# ENDPOINT ATTACHMENTS (PSC)
# ==============================================================================

variable "endpoint_attachments" {
  description = <<-EOT
    Map of Apigee endpoint attachments for connecting to backend services via PSC.

    Example:
      endpoint_attachments = {
        gateway-backend = {
          region             = "us-east4"
          service_attachment = "projects/PROJECT/regions/REGION/serviceAttachments/NAME"
        }
      }
  EOT
  type = map(object({
    region             = string
    service_attachment = string
  }))
  default = {}
}

# ==============================================================================
# SERVICE ACCOUNTS
# ==============================================================================

variable "create_service_accounts" {
  description = "Create service accounts for Apigee proxy runtime"
  type        = bool
  default     = true
}

variable "service_account_prefix" {
  description = "Prefix for service account names"
  type        = string
  default     = "apigee"
}

# ==============================================================================
# APIM OPERATOR CONFIGURATION
# ==============================================================================

variable "create_apim_operator_iam" {
  description = "Create IAM bindings for Apigee APIM Operator"
  type        = bool
  default     = true
}

variable "enable_apim_workload_identity" {
  description = "Enable the Workload Identity IAM binding for the APIM Operator (requires GKE Workload Identity Pool to exist)"
  type        = bool
  default     = true
}

variable "apim_operator_namespace" {
  description = "Kubernetes namespace for APIM Operator service account"
  type        = string
  default     = "apim"
}

variable "apim_operator_ksa" {
  description = "Kubernetes service account name for APIM Operator"
  type        = string
  default     = "apim-ksa"
}

# ==============================================================================
# NORTHBOUND LOAD BALANCER CONFIGURATION
# ==============================================================================

variable "northbound_lb" {
  description = <<-EOT
    Northbound load balancer configuration for PSC-based Apigee access.
    Creates a regional internal HTTPS LB fronting Apigee via a PSC NEG.

    Attributes:
    - network_self_link: VPC network self link for the LB
    - subnet_self_link: Subnet self link for the LB frontend IP
    - ssl_certificate_id: Certificate Manager certificate ID for HTTPS
    - instances: Map of region names to peer (must match var.instances keys)
  EOT
  type = object({
    network_self_link  = string
    subnet_self_link   = string
    ssl_certificate_id = string
    instances          = map(object({}))
  })
  default = null
}

# ==============================================================================
# SOUTHBOUND DNS PEERING CONFIGURATION
# ==============================================================================

variable "dns_peering_zones" {
  description = <<-EOT
    Map of DNS peering zones for Apigee southbound connectivity.
    Allows Apigee (in PSC mode) to resolve hostnames in customer private DNS zones.

    Example:
      dns_peering_zones = {
        internal-zone = {
          domain            = "internal.example.com"
          description       = "Internal DNS zone"
          target_project_id = "my-project"
          target_network_id = "projects/my-project/global/networks/my-vpc"
        }
      }
  EOT
  type = map(object({
    domain            = string
    description       = optional(string, "Terraform-managed Apigee DNS peering zone")
    target_project_id = string
    target_network_id = string
  }))
  default = {}
}

# ==============================================================================
# SOUTHBOUND WILDCARD DNS RECORD
# ==============================================================================

variable "internal_dns_wildcard" {
  description = "Wildcard DNS record configuration for Apigee internal endpoint resolution"
  type = object({
    managed_zone        = string
    domain              = string
    endpoint_attachment = string
    ttl                 = optional(number, 300)
  })
  default = null
}
