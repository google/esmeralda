# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

resource "random_id" "suffix" {
  byte_length = 2
}

locals {
  # The final project_id, which will include the random suffix if a new project is created.
  project_id = var.create_project ? module.foundation[0].project_id : var.project_id

  # This conditional suffix is passed to the foundation module. It ensures that a
  # random suffix is appended to the project_id ONLY when creating a new project.
  # For existing projects, it's an empty string, leaving the project_id unchanged.
  # Other resources that need a guaranteed unique suffix (like GCS buckets) should
  # use random_id.suffix.hex directly.
  random_suffix = var.create_project ? random_id.suffix.hex : ""
}

# --- Data Sources ---
data "google_project" "project_details" {
  count      = var.create_project ? 0 : 1
  project_id = local.project_id
}

# --- Module for GCP Project Creation (Optional) ---
module "foundation" {
  source = "./modules/foundation"
  count  = var.create_project ? 1 : 0 # Only create if var.create_project is true

  project_id      = var.project_id
  random_suffix   = local.random_suffix
  org_id          = var.org_id
  folder_id       = var.folder_id
  billing_account = var.billing_account
  apis_to_enable = [
    "agentregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "apihub.googleapis.com",
    "apphub.googleapis.com",
    "apptopology.googleapis.com",
    "artifactregistry.googleapis.com",
    "certificatemanager.googleapis.com",
    "cloudapiregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "dataform.googleapis.com",
    "dns.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "networksecurity.googleapis.com",
    "networkservices.googleapis.com",
    "notebooks.googleapis.com",
    "observability.googleapis.com",
    "run.googleapis.com",
    "storage-component.googleapis.com",
    "storage.googleapis.com",
    "telemetry.googleapis.com",
    "texttospeech.googleapis.com",
    "serviceusage.googleapis.com",
    "bigquery.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com"
  ]
  
  enable_psc_interface = true
}

# --- CI/CD Module (Cloud Build & Artifact Registry) ---
module "ci_cd" {
  source = "./modules/ci-cd"
  depends_on = [module.foundation]

  project_id               = local.project_id
  project_number           = var.create_project ? module.foundation[0].project_number : data.google_project.project_details[0].number
  region                   = var.region
  agent_name               = var.agent_name
  agent_engine_bucket_name = module.data_and_logging.bucket_names["agent-engine"]
}

# --- Data and Logging Module (GCS & BigQuery) ---
module "data_and_logging" {
  source = "./modules/data-and-logging"
  depends_on = [module.foundation]

  project_id     = local.project_id
  project_number = var.create_project ? module.foundation[0].project_number : data.google_project.project_details[0].number
  region         = var.region
  bucket_names = {
    "agent-engine"               = "${var.project_id}-agent-engine-${random_id.suffix.hex}",
    "bucket-adk-agent-artifacts" = "${var.project_id}-bucket-adk-agent-artifacts-${random_id.suffix.hex}",
    "agent-logs-offload"         = "${var.project_id}-agent-logs-offload-${random_id.suffix.hex}"
  }
}

# --- API Hub Module ---
module "apihub" {
  source = "./modules/apihub"

  project_id          = local.project_id
  region              = var.region
  api_hub_instance_id = "default-instance-${random_id.suffix.hex}"


  depends_on = [
    module.foundation
  ]
}

# --- Networking ---
module "networking" {
  source = "./modules/networking"

  project_id  = local.project_id
  region      = var.region
  name_prefix = "gateway"
  vpc_name    = "gateway-vpc"
  subnet_name = "gke-subnet"
  
  primary_subnet_cidr = "10.0.0.0/20"
  pods_cidr           = "10.4.0.0/14"
  services_cidr       = "10.8.0.0/20"
  proxy_subnet_cidr   = "10.9.0.0/24"
  psc_subnet_cidr     = "10.10.0.0/24"
  
  pods_range_name     = "pods"
  services_range_name = "services"
  
  gateway_scope = "regional"
  
  enable_psc_interface      = true
  psc_interface_subnet_cidr = "10.11.0.0/28"
  psc_interface_dns_zone = {
    name   = "internal-gateway"
    domain = "internal.gateway."
  }
  
  depends_on = [module.foundation]
}

module "internal_load_balancer" {
  source = "./modules/internal-load-balancer"

  project_id           = local.project_id
  region               = var.region
  network_self_link    = module.networking.network_self_link
  subnetwork_self_link = module.networking.subnet_self_link
  ip_address           = module.networking.internal_gateway_ip

  depends_on = [module.foundation, module.networking]
}

# --- Cloud SQL (A2A Task Store) ---
provider "postgresql" {
  scheme   = "gcppostgres"
  host     = module.cloud_sql.instance_connection_name
  port     = 5432
  username = module.cloud_sql.bootstrap_user
  password = module.cloud_sql.bootstrap_password
  sslmode  = "disable"
}

module "cloud_sql" {
  source = "./modules/cloud-sql"

  project_id            = local.project_id
  region                = var.region
  vpc_id                = module.networking.network_id
  agent_service_account = "test-vm-sa@${local.project_id}.iam"

  depends_on = [module.foundation, module.networking]
}

# --- Firewall Rule for IAP SSH ---
resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "allow-iap-ssh"
  project = local.project_id
  network = module.networking.network_self_link

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"] # Google IAP IP range
}

# --- Test VM for VPC Connectivity ---
resource "google_service_account" "test_vm_sa" {
  project      = local.project_id
  account_id   = "test-vm-sa"
  display_name = "Test VM Service Account"
}

# Grant run.invoker to the Test VM so it can call Cloud Run internally
resource "google_project_iam_member" "test_vm_run_invoker" {
  project = local.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

# Grant Token Creator to the SA so it can generate its own OIDC tokens via IAM API
resource "google_service_account_iam_member" "test_vm_token_creator" {
  service_account_id = google_service_account.test_vm_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

# Grant all required Agent Engine roles to the SA
locals {
  agent_roles = [
    "roles/aiplatform.user",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/telemetry.writer",
    "roles/bigquery.jobUser",
    "roles/bigquery.dataEditor",
    "roles/storage.objectAdmin"
  ]
}

resource "google_project_iam_member" "test_vm_agent_roles" {
  for_each = toset(local.agent_roles)
  project  = local.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

resource "google_compute_instance" "test_vm" {
  name         = "test-vm"
  project      = local.project_id
  machine_type = "e2-micro"
  zone         = module.networking.available_zones[0]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network    = module.networking.network_self_link
    subnetwork = module.networking.subnet_self_link
    # No access_config block means no public IP, it's fully private
  }
  
  # Attach the dedicated service account with run.invoker permissions
  service_account {
    email  = google_service_account.test_vm_sa.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  allow_stopping_for_update = true

  depends_on = [module.networking]
}
