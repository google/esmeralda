# infrastructure/modules/2-networking/main.tf

# Resolve dynamically generated project numbers to avoid manual inputs and automation locks
data "google_project" "mcps" {
  project_id = var.mcps_project_id
}

data "google_project" "a2a" {
  project_id = var.a2a_project_id
}

data "google_project" "root_agent" {
  project_id = var.root_project_id
}

data "google_project" "gateway" {
  project_id = var.gateway_project_id
}

# ====================================================================
# 1. SHARED VPC HOST & SERVICE PROJECTS ATTACHMENTS
# ====================================================================

# Enable Shared VPC host mode on the host project (skipped if BYO)
resource "google_compute_shared_vpc_host_project" "host" {
  count   = var.byo_net_host_project ? 0 : 1
  project = var.net_host_project_id
}

# Attach the MCP Central Tools project as a service project
resource "google_compute_shared_vpc_service_project" "mcps" {
  host_project    = var.net_host_project_id
  service_project = var.mcps_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Core AI Platform project as a service project
resource "google_compute_shared_vpc_service_project" "a2a" {
  host_project    = var.net_host_project_id
  service_project = var.a2a_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Line-of-Business Root Agent project as a service project
resource "google_compute_shared_vpc_service_project" "root_agent" {
  host_project    = var.net_host_project_id
  service_project = var.root_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Gateway Ingress project conditionally as a service project
resource "google_compute_shared_vpc_service_project" "gateway" {
  count           = var.byo_gateway_project ? 0 : 1
  host_project    = var.net_host_project_id
  service_project = var.gateway_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Governance project conditionally as a service project
resource "google_compute_shared_vpc_service_project" "governance" {
  count           = var.byo_governance_project ? 0 : 1
  host_project    = var.net_host_project_id
  service_project = var.governance_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# ====================================================================
# 2. GREENFIELD SHARED VPC NETWORKING CREATION
# ====================================================================

# ====================================================================
# 2. GREENFIELD SHARED VPC NETWORKING CREATION
# ====================================================================

# Provision Shared VPC (Only if greenfield)
resource "google_compute_network" "shared_vpc" {
  count                   = var.byo_networking ? 0 : 1
  name                    = "vpc-esmeralda-shared-${var.environment}"
  project                 = var.net_host_project_id
  auto_create_subnetworks = false
}

# Core Subnet for Workloads
resource "google_compute_subnetwork" "core" {
  count                    = var.byo_networking ? 0 : 1
  name                     = "sb-esmeralda-core-${var.environment}"
  project                  = var.net_host_project_id
  region                   = var.region
  network                  = google_compute_network.shared_vpc[0].id
  ip_cidr_range            = "10.0.1.0/24"
  private_ip_google_access = true
}

# Proxy-only Subnet for regional load balancing (Internal Load Balancer/Kong/Apigee)
resource "google_compute_subnetwork" "proxy" {
  count         = var.byo_networking ? 0 : 1
  name          = "sb-esmeralda-proxy-${var.environment}"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.9.0.0/24"
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# PSC Subnet for Private Service Connect Endpoint bindings
resource "google_compute_subnetwork" "psc" {
  count         = var.byo_networking ? 0 : 1
  name          = "sb-esmeralda-psc-${var.environment}"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.10.0.0/24"
}

# PSC Interface Subnet for Serverless Agent Incoming Tunnels
resource "google_compute_subnetwork" "psc_interface" {
  count                    = !var.byo_networking && var.enable_psc_interface ? 1 : 0
  name                     = "sb-esmeralda-psc-interface-${var.environment}"
  project                  = var.net_host_project_id
  region                   = var.region
  network                  = google_compute_network.shared_vpc[0].id
  ip_cidr_range            = "10.11.0.0/24"
  private_ip_google_access = true
}

# Private Service Connection (PSA) range for Cloud SQL Peering
resource "google_compute_global_address" "sql_peering" {
  count         = var.byo_networking ? 0 : 1
  name          = "sql-peering-range-${var.environment}"
  project       = var.net_host_project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.shared_vpc[0].id
}

resource "google_service_networking_connection" "sql_connection" {
  count                   = var.byo_networking ? 0 : 1
  network                 = google_compute_network.shared_vpc[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_peering[0].name]
}

# Cloud Router & NAT for Outbound API access
resource "google_compute_router" "router" {
  count   = var.byo_networking ? 0 : 1
  name    = "cr-esmeralda-nat-${var.environment}"
  project = var.net_host_project_id
  region  = var.region
  network = google_compute_network.shared_vpc[0].id
}

resource "google_compute_router_nat" "nat" {
  count                              = var.byo_networking ? 0 : 1
  name                               = "nat-esmeralda-outbound-${var.environment}"
  project                            = var.net_host_project_id
  router                             = google_compute_router.router[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# ====================================================================
# 3. PRIVATE SERVICE CONNECT (PSC) NETWORK ATTACHMENT
# ====================================================================

# Compute Network Attachment allowing Serverless Reasoning engines auto acceptance
resource "google_compute_network_attachment" "psc_interface" {
  count                 = var.enable_psc_interface ? 1 : 0
  project               = var.net_host_project_id
  name                  = "gateway-psc-interface-attachment-${var.environment}"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.byo_networking ? var.existing_subnet_id : try(google_compute_subnetwork.psc_interface[0].self_link, "")]
}

# Ingress Firewall to accept traffic from the PSC Interface Subnet
resource "google_compute_firewall" "psc_interface_allow" {
  count         = var.enable_psc_interface && !var.byo_networking ? 1 : 0
  project       = var.net_host_project_id
  name          = "allow-psc-interface-ingress-${var.environment}"
  network       = google_compute_network.shared_vpc[0].id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["10.11.0.0/24"]

  allow {
    protocol = "tcp"
    ports    = ["22", "80", "443"]
  }
  allow {
    protocol = "icmp"
  }
}

# Allow IAP secure tunnel to reach private instances on SSH port 22
resource "google_compute_firewall" "allow_iap_ssh" {
  count         = var.byo_networking ? 0 : 1
  project       = var.net_host_project_id
  name          = "allow-iap-ssh-${var.environment}"
  network       = google_compute_network.shared_vpc[0].id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}


# ====================================================================
# 4. SECURE WEB PROXY (SWP) egres filtering
# ====================================================================

# Deploy SWP at IP 10.0.1.100 (100th IP of Core subnet)
module "secure_web_proxy" {
  count      = var.enable_secure_web_proxy && !var.byo_networking ? 1 : 0
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/net-swp?ref=v53.0.0"
  project_id = var.net_host_project_id
  region     = var.region
  name       = "gateway-swp-${var.environment}"
  network    = google_compute_network.shared_vpc[0].id
  subnetwork = google_compute_subnetwork.core[0].id
  
  gateway_config = {
    addresses = ["10.0.1.100"]
  }
  
  policy_rules = {
    allow-all = {
      priority        = 1000
      session_matcher = "host() != ''"
      basic_profile   = "ALLOW"
    }
  }

  depends_on = [google_compute_subnetwork.proxy]
}

# ====================================================================
# 5. SUBNET-LEVEL SERVICE AGENT IAM NETWORK USER BINDINGS
# ====================================================================

locals {
  target_vpc_id    = var.byo_networking ? var.existing_vpc_id : try(google_compute_network.shared_vpc[0].id, "")
  target_subnet_id = var.byo_networking ? var.existing_subnet_id : try(google_compute_subnetwork.core[0].id, "")
  
  # Parse subnet name and region from the subnet ID URI
  subnet_parsed_name   = element(split("/", local.target_subnet_id), length(split("/", local.target_subnet_id)) - 1)
  subnet_parsed_region = element(split("/", local.target_subnet_id), length(split("/", local.target_subnet_id)) - 3)

  # Consolidate all Service accounts that need Subnet Network User access
  subnet_network_users = [
    # 1. MCP Central Tools Project Service SAs
    "serviceAccount:${var.mcps_run_service_agent}",

    # 2. Core AI Platform Project Service SAs
    "serviceAccount:${var.a2a_run_service_agent}",
    "serviceAccount:${var.a2a_vertex_service_agent}",

    # 3. LOB Root Agent Project Service SAs
    "serviceAccount:${var.root_vertex_service_agent}",

    # 4. Gateway Project Service SAs
    "serviceAccount:${var.gateway_run_service_agent}",

    # 5. Vertex AI Reasoning Engine Service Agents (-re)
    "serviceAccount:${replace(var.a2a_vertex_service_agent, "@gcp-sa-aiplatform.iam.gserviceaccount.com", "@gcp-sa-aiplatform-re.iam.gserviceaccount.com")}",
    "serviceAccount:${replace(var.root_vertex_service_agent, "@gcp-sa-aiplatform.iam.gserviceaccount.com", "@gcp-sa-aiplatform-re.iam.gserviceaccount.com")}"
  ]
}

# Delay to allow newly generated service agent identities from Stage 1 to fully propagate to Google's global IAM servers
resource "time_sleep" "iam_propagation" {
  create_duration = "30s"

  depends_on = [
    data.google_project.mcps,
    data.google_project.a2a,
    data.google_project.root_agent,
    data.google_project.gateway
  ]
}

# Grant compute.networkUser on the Core Workload subnet to all service project robots
resource "google_compute_subnetwork_iam_member" "network_users" {
  for_each   = toset(local.subnet_network_users)
  project    = var.net_host_project_id
  region     = local.subnet_parsed_region
  subnetwork = local.subnet_parsed_name
  role       = "roles/compute.networkUser"
  member     = each.value

  depends_on = [time_sleep.iam_propagation]
}

# Grant compute.networkUser on the PSC Interface subnet to all service project robots
resource "google_compute_subnetwork_iam_member" "psc_interface_users" {
  for_each   = !var.byo_networking && var.enable_psc_interface ? toset(local.subnet_network_users) : toset([])
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = google_compute_subnetwork.psc_interface[0].name
  role       = "roles/compute.networkUser"
  member     = each.value

  depends_on = [time_sleep.iam_propagation]
}

# Grant compute.networkUser on the Host Project to all service project robots for PSC Network Attachment access
resource "google_project_iam_member" "host_network_users" {
  for_each = var.enable_psc_interface ? toset(local.subnet_network_users) : toset([])
  project  = var.net_host_project_id
  role     = "roles/compute.networkUser"
  member   = each.value

  depends_on = [time_sleep.iam_propagation]
}

# ====================================================================
# 6. PRIVATE DNS SERVICE DISCOVERY ZONES
# ====================================================================

# A. Central Private Zone for inter-agent communication
resource "google_dns_managed_zone" "private_dns" {
  name        = "esmeralda-private-dns-${var.environment}"
  project     = var.net_host_project_id
  dns_name    = "esmeralda.internal."
  description = "Central Private DNS Zone for Esmeralda micro-agent communications"

  visibility = "private"

  private_visibility_config {
    networks {
      network_url = local.target_vpc_id
    }
  }
}

# B. Dedicated Private Zone for Gateway & SWP Resolution Peering
module "psc_interface_dns_zone" {
  count      = var.enable_psc_interface && !var.byo_networking ? 1 : 0
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/dns?ref=v53.0.0"
  project_id = var.net_host_project_id
  name       = "internal-gateway-${var.environment}"
  zone_config = {
    domain = "internal.gateway."
    private = {
      client_networks = [local.target_vpc_id]
    }
  }
  recordsets = {
    # Resolve wildcard gateway endpoints to the future internal gateway IP placeholder
    "A *"   = { records = ["10.0.1.200"] } # Placeholder for regional load balancer VIP
    # Resolve swp to the Secure Web Proxy IP
    "A swp" = { records = ["10.0.1.100"] }
  }
}
