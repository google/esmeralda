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

variable "project_id" {
  description = "The GCP project ID"
  type        = string
}

variable "region" {
  description = "The GCP region for resources"
  type        = string
}

variable "vpc_name" {
  description = "Name of the VPC network"
  type        = string
}

variable "subnet_name" {
  description = "Name of the primary subnet"
  type        = string
}

variable "pods_range_name" {
  description = "Name of the secondary range for pods"
  type        = string
  default     = "pods"
}

variable "services_range_name" {
  description = "Name of the secondary range for services"
  type        = string
  default     = "services"
}

variable "name_prefix" {
  description = "Prefix for resource names (typically cluster name)"
  type        = string
}

variable "primary_subnet_cidr" {
  description = "CIDR range for the primary subnet"
  type        = string
  default     = "10.0.0.0/20"
}

variable "pods_cidr" {
  description = "CIDR range for the pods secondary range"
  type        = string
  default     = "10.4.0.0/14"
}

variable "services_cidr" {
  description = "CIDR range for the services secondary range"
  type        = string
  default     = "10.8.0.0/20"
}

variable "proxy_subnet_cidr" {
  description = "CIDR range for the proxy-only subnet"
  type        = string
  default     = "10.9.0.0/24"
}

variable "psc_subnet_cidr" {
  description = "CIDR range for the Private Service Connect subnet"
  type        = string
  default     = "10.10.0.0/24"
}

variable "gateway_scope" {
  description = "Gateway scope: 'regional' for regional internal gateway, or null to skip gateway IP provisioning"
  type        = string
  default     = "regional"
  validation {
    condition     = var.gateway_scope == null || contains(["regional"], var.gateway_scope)
    error_message = "gateway_scope must be 'regional' or null"
  }
}

variable "apigee_internal_dns_zone" {
  description = "Configuration for Apigee internal DNS zone (private zone with no VPC attachment, used solely for Apigee DNS peering)"
  type = object({
    name   = optional(string, "apigee-internal-zone")
    domain = string
  })
  default = null
}

variable "enable_psc_interface" {
  description = "Enable PSC Interface resources (subnet, network attachment, firewall rules)"
  type        = bool
  default     = false
}

variable "psc_interface_subnet_cidr" {
  description = "CIDR for the PSC Interface subnet (min /28)"
  type        = string
  default     = "10.11.0.0/28"
}

variable "psc_interface_dns_zone" {
  description = "Private DNS zone for PSC Interface DNS peering"
  type = object({
    name   = optional(string, "psc-interface-dns-zone")
    domain = string
  })
  default = null
}
