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

/**
 * Networking Module
 *
 * Creates VPC network, subnets, Cloud NAT, and static IP addresses for gateways.
 * Supports regional internal gateway configuration.
 */

# Get available zones in the region
data "google_compute_zones" "available" {
  project = var.project_id
  region  = var.region
}

# VPC Network
module "vpc" {
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/net-vpc?ref=v53.0.0"
  project_id = var.project_id
  name       = var.vpc_name

  subnets = [
    {
      name          = var.subnet_name
      region        = var.region
      ip_cidr_range = var.primary_subnet_cidr
    }
  ]

  subnets_proxy_only = [
    {
      name          = "${var.name_prefix}-proxy-subnet"
      region        = var.region
      ip_cidr_range = var.proxy_subnet_cidr
      active        = true
    }
  ]

  subnets_psc = [
    {
      name          = "${var.name_prefix}-psc-subnet"
      region        = var.region
      ip_cidr_range = var.psc_subnet_cidr
    }
  ]
}

# Cloud Router for NAT
resource "google_compute_router" "nat_router" {
  name    = "${var.name_prefix}-nat-router"
  project = var.project_id
  network = module.vpc.self_link
  region  = var.region
}

# Cloud NAT for outbound internet access from private nodes
resource "google_compute_router_nat" "nat_gateway" {
  name                               = "${var.name_prefix}-nat-gateway"
  project                            = var.project_id
  router                             = google_compute_router.nat_router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Internal static IP addresses for internal L7 load balancers
# Purpose must be SHARED_LOADBALANCER_VIP for Gateway API compatibility
resource "google_compute_address" "internal_gateway" {
  count        = var.gateway_scope == "regional" ? 1 : 0
  name         = "${var.name_prefix}-internal-gateway-ip"
  project      = var.project_id
  region       = var.region
  subnetwork   = module.vpc.subnet_self_links["${var.region}/${var.subnet_name}"]
  address_type = "INTERNAL"
  purpose      = "SHARED_LOADBALANCER_VIP"
  description  = "Internal static IP for internal gateway"
}

# Private DNS zone for Apigee internal resolution (no VPC attachment)
# This zone is consumed by Apigee via DNS peering, not by VPC workloads
module "apigee_internal_dns_zone" {
  count      = var.apigee_internal_dns_zone != null ? 1 : 0
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/dns?ref=v53.0.0"
  project_id = var.project_id
  name       = var.apigee_internal_dns_zone.name
  zone_config = {
    domain = var.apigee_internal_dns_zone.domain
    private = {
      client_networks = []
    }
  }
}

# PSC Interface — dedicated regular subnet for network attachment
resource "google_compute_subnetwork" "psc_interface" {
  count         = var.enable_psc_interface ? 1 : 0
  project       = var.project_id
  name          = "${var.name_prefix}-psc-interface-subnet"
  region        = var.region
  network       = module.vpc.self_link
  ip_cidr_range = var.psc_interface_subnet_cidr
}

# PSC Interface — network attachment with automatic acceptance
resource "google_compute_network_attachment" "psc_interface" {
  count                 = var.enable_psc_interface ? 1 : 0
  project               = var.project_id
  name                  = "${var.name_prefix}-psc-interface-attachment"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.psc_interface[0].self_link]
}

# PSC Interface — firewall rule allowing ingress from PSC-I subnet
resource "google_compute_firewall" "psc_interface_allow" {
  count         = var.enable_psc_interface ? 1 : 0
  project       = var.project_id
  name          = "${var.name_prefix}-allow-psc-interface"
  network       = module.vpc.self_link
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = [var.psc_interface_subnet_cidr]

  allow {
    protocol = "tcp"
    ports    = ["22", "443"]
  }
  allow {
    protocol = "icmp"
  }
}

# PSC Interface — private DNS zone for DNS peering
module "psc_interface_dns_zone" {
  count      = var.enable_psc_interface && var.psc_interface_dns_zone != null ? 1 : 0
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/dns?ref=v53.0.0"
  project_id = var.project_id
  name       = var.psc_interface_dns_zone.name
  zone_config = {
    domain = var.psc_interface_dns_zone.domain
    private = {
      client_networks = [module.vpc.self_link]
    }
  }
  recordsets = {
    "A *" = { records = [google_compute_address.internal_gateway[0].address] }
    "A swp" = { records = [cidrhost(var.primary_subnet_cidr, 100)] }
  }
}

# --- Secure Web Proxy (SWP) ---
# An explicit proxy is required for Agent Engines using PSC-I to go to the Internet.
module "secure_web_proxy" {
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/net-swp?ref=v53.0.0"
  project_id = var.project_id
  region     = var.region
  name       = "${var.name_prefix}-swp"
  network    = module.vpc.id
  subnetwork = module.vpc.subnet_ids["${var.region}/${var.subnet_name}"]
  gateway_config = {
    addresses = [cidrhost(var.primary_subnet_cidr, 100)]
  }
  policy_rules = {
    allow-all = {
      priority        = 1000
      session_matcher = "host() != ''"
      basic_profile   = "ALLOW"
    }
  }
}
