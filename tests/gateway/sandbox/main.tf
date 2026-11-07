# Gateway sandbox: one throwaway project that runs gateways/envoy for real, behind the entrypoint's internal HTTPS LB,
# with a private-CA certificate, private DNS, a Cloud Run upstream and a test VM that calls it.
# See README.md. State holds the sandbox CA key: keep it outside git (backend path at init).
terraform {
  required_version = ">= 1.6"
  backend "local" {}
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0"
    }
  }
}

provider "google" {
  region = var.region
}

resource "random_id" "suffix" {
  byte_length = 3
}

resource "google_project" "sandbox" {
  name            = "Esmeralda gateway sandbox"
  project_id      = "${var.project_prefix}-${random_id.suffix.hex}"
  folder_id       = var.folder_id
  billing_account = var.billing_account
  deletion_policy = "DELETE"
  labels = {
    purpose = "esmeralda-gateway-sandbox"
  }
}

resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "compute.googleapis.com",
    "dns.googleapis.com",
    "iap.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  project            = google_project.sandbox.project_id
  service            = each.key
  disable_on_destroy = false
}

locals {
  project = google_project.sandbox.project_id
  zone    = "${var.region}-a"

  # The image apps/services/envoy/Dockerfile pins (repo:tag@digest), deployed by digest, so a bump there
  # also moves the sandbox.
  dockerfile_image = regex("(?m)^FROM\\s+(\\S+)", file("${path.module}/../../../apps/services/envoy/Dockerfile"))[0]
  gateway_image = var.gateway_image != "" ? var.gateway_image : format(
    "docker.io/%s@%s", split(":", split("@", local.dockerfile_image)[0])[0], split("@", local.dockerfile_image)[1]
  )
}

# --- Network: core subnet (Private Google Access for run.app and googleapis), proxy-only subnet, IAP SSH.

resource "google_compute_network" "vpc" {
  name                    = "vpc-sandbox"
  project                 = local.project
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "core" {
  name                     = "sb-core"
  project                  = local.project
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.0.1.0/24"
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "proxy" {
  name          = "sb-proxy"
  project       = local.project
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.9.0.0/24"
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "allow-iap-ssh"
  project       = local.project
  network       = google_compute_network.vpc.id
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["test-vm"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_dns_managed_zone" "internal" {
  name       = "esmeralda-internal"
  project    = local.project
  dns_name   = "esmeralda.internal."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }
  depends_on = [google_project_service.apis]
}

# --- Identities: the gateway (what upstreams see) and the caller (the test VM).

resource "google_service_account" "gateway" {
  project      = local.project
  account_id   = "sa-esmeralda-gateway-sbx"
  display_name = "Envoy gateway (sandbox)"
}

resource "google_service_account" "caller" {
  project      = local.project
  account_id   = "sa-esmeralda-caller-sbx"
  display_name = "Gateway caller and test VM (sandbox)"
}

resource "google_project_iam_member" "gateway_aiplatform_user" {
  project = local.project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

# --- Private CA, as layer 3 creates it.

resource "tls_private_key" "ca" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem       = tls_private_key.ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 720
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]
  subject {
    common_name  = "Esmeralda Sandbox Root CA"
    organization = "Esmeralda sandbox"
  }
}

# --- Upstream: a stand-in MCP server / streaming agent on Cloud Run, private like the real ones.

resource "google_cloud_run_v2_service" "upstream" {
  name                = "gateway-upstream"
  project             = local.project
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  template {
    timeout = "300s"
    scaling {
      max_instance_count = 2
    }
    containers {
      image   = var.upstream_image
      command = ["python3"]
      args    = ["-c", file("${path.module}/upstream.py")]
      ports {
        container_port = 8080
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "upstream_invoker" {
  project  = local.project
  location = var.region
  name     = google_cloud_run_v2_service.upstream.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gateway.email}"
}

# --- The gateway under test.

module "gateway" {
  source = "../../../infrastructure/modules/5-workloads/gateways/envoy"

  project_id               = local.project
  region                   = var.region
  vpc_id                   = google_compute_network.vpc.id
  subnet_id                = google_compute_subnetwork.core.id
  environment              = "sbx"
  gateway_image            = local.gateway_image
  gateway_sa_email         = google_service_account.gateway.email
  invoker_service_accounts = [google_service_account.caller.email]
  min_instances            = 0
  max_instances            = 1 # one instance, so the per-instance rate limit is exact in checks.sh
  rate_limit_per_minute    = var.rate_limit_per_minute

  agent_endpoints = {
    # ID token for a Cloud Run upstream, like the MCP servers.
    legacy-dms = {
      logical_name = "legacy-dms"
      engine_id    = google_cloud_run_v2_service.upstream.name
      endpoint_url = google_cloud_run_v2_service.upstream.uri
      audience     = google_cloud_run_v2_service.upstream.uri
      probe        = "mcp"
    }
    # OAuth2 access token for Vertex AI, like the A2A specialist (here: list reasoning engines).
    ai-coe-mortgage-specialist = {
      logical_name = "ai-coe-mortgage-specialist"
      engine_id    = "vertex-ai"
      endpoint_url = "https://${var.region}-aiplatform.googleapis.com/v1/projects/${local.project}/locations/${var.region}"
      audience     = "https://${var.region}-aiplatform.googleapis.com"
    }
    # SSE stream through the path_map route, like the orchestrator's :streamQuery?alt=sse.
    cx-mortgage-orchestrator = {
      logical_name = "cx-mortgage-orchestrator"
      engine_id    = google_cloud_run_v2_service.upstream.name
      endpoint_url = "${google_cloud_run_v2_service.upstream.uri}/v1beta1/projects/sandbox/locations/${var.region}/reasoningEngines/456"
      audience     = google_cloud_run_v2_service.upstream.uri
      path_map     = { "/api/stream_reasoning_engine" = ":streamQuery?alt=sse" }
      streaming    = true
    }
  }

  depends_on = [google_project_service.apis]
}

# --- The front door, as in an environment: reserved VIP + DNS (layer 2), a private-CA leaf (layer 3)
# and the layer-5 entrypoint module routing every host to the Envoy adapter's backend service.

resource "google_compute_address" "entrypoint" {
  name         = "ilb-esmeralda-vip-sbx"
  project      = local.project
  region       = var.region
  subnetwork   = google_compute_subnetwork.core.id
  address_type = "INTERNAL"
}

resource "google_dns_record_set" "entrypoint" {
  for_each     = toset(["esmeralda.internal.", "*.esmeralda.internal."])
  project      = local.project
  managed_zone = google_dns_managed_zone.internal.name
  name         = each.key
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.entrypoint.address]
}

resource "tls_private_key" "entrypoint" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "entrypoint" {
  private_key_pem = tls_private_key.entrypoint.private_key_pem
  subject {
    common_name  = "*.esmeralda.internal"
    organization = "Esmeralda sandbox"
  }
  dns_names = ["esmeralda.internal", "*.esmeralda.internal"]
}

resource "tls_locally_signed_cert" "entrypoint" {
  cert_request_pem      = tls_cert_request.entrypoint.cert_request_pem
  ca_private_key_pem    = tls_private_key.ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.ca.cert_pem
  validity_period_hours = 720
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "google_compute_region_ssl_certificate" "entrypoint" {
  name_prefix = "cert-esmeralda-ilb-sbx-"
  project     = local.project
  region      = var.region
  private_key = tls_private_key.entrypoint.private_key_pem
  certificate = "${tls_locally_signed_cert.entrypoint.cert_pem}${tls_self_signed_cert.ca.cert_pem}"
  lifecycle {
    create_before_destroy = true
  }
  depends_on = [google_project_service.apis]
}

module "entrypoint" {
  source = "../../../infrastructure/modules/5-workloads/entrypoint"

  project_id         = local.project
  region             = var.region
  environment        = "sbx"
  vpc_id             = google_compute_network.vpc.id
  subnet_id          = google_compute_subnetwork.core.id
  ip_address         = google_compute_address.entrypoint.address
  domain             = "esmeralda.internal"
  ssl_certificate_id = google_compute_region_ssl_certificate.entrypoint.id
  # Every host goes to the Envoy adapter (no direct backends in the sandbox), so callers send ID tokens.
  catalog = {
    legacy-dms                 = { kind = "mcp", token = "id", probe = "mcp" }
    ai-coe-mortgage-specialist = { kind = "a2a", token = "access", probe = "none" }
    cx-mortgage-orchestrator   = { kind = "adk", token = "access", probe = "none" }
  }
  adapters        = { envoy = module.gateway.backend_service_id }
  default_backend = "envoy"

  depends_on = [google_compute_subnetwork.proxy]
}

# --- Test VM: no external IP; reached over IAP SSH; trusts the sandbox CA through instance metadata.

resource "google_compute_instance" "test_vm" {
  name         = "test-vm-sbx"
  project      = local.project
  zone         = local.zone
  machine_type = "e2-micro"
  tags         = ["test-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.core.id
  }

  service_account {
    email  = google_service_account.caller.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot = true
  }

  metadata = {
    enable-oslogin    = "TRUE"
    esmeralda-root-ca = tls_self_signed_cert.ca.cert_pem
  }

  depends_on = [google_project_service.apis]
}
