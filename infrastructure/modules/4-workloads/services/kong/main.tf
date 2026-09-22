terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0"
    }
  }
}

# Compile the declarative kong.yml file dynamically based on agent_endpoints
locals {
  kong_config = templatefile("${path.module}/templates/kong.yml.tpl", {
    agent_endpoints = var.agent_endpoints
  })
}

# Securely store the compiled Kong declarative configuration inside Secret Manager
resource "google_secret_manager_secret" "kong_config" {
  secret_id = "kong-config-${var.environment}"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "kong_config" {
  secret      = google_secret_manager_secret.kong_config.id
  secret_data = local.kong_config
}



# Resolve pre-created Service Account for Kong from Stage 3 Security
data "google_service_account" "kong_sa" {
  account_id = "kong-gateway-sa-${var.environment}"
  project    = var.project_id
}

# Allow Kong Service Account to read declarative configuration from Secret Manager
resource "google_secret_manager_secret_iam_member" "kong_sa_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.kong_config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_service_account.kong_sa.email}"
}

# Deploy Kong Gateway on Cloud Run with internal-only ingress
resource "google_cloud_run_v2_service" "kong_gateway" {
  name                = "kong-gateway-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  custom_audiences = [
    "http://esmeralda.internal",
    "https://esmeralda.internal",
    "esmeralda.internal",
    "http://a2a-mortgage-agent.esmeralda.internal",
    "https://a2a-mortgage-agent.esmeralda.internal",
    "a2a-mortgage-agent.esmeralda.internal",
    "http://root-agent.esmeralda.internal",
    "https://root-agent.esmeralda.internal",
    "root-agent.esmeralda.internal",
    "http://legacy-dms.esmeralda.internal",
    "https://legacy-dms.esmeralda.internal",
    "legacy-dms.esmeralda.internal",
    "http://income-verification.esmeralda.internal",
    "https://income-verification.esmeralda.internal",
    "income-verification.esmeralda.internal",
    "http://corporate-email.esmeralda.internal",
    "https://corporate-email.esmeralda.internal",
    "corporate-email.esmeralda.internal"
  ]

  depends_on = [
    google_secret_manager_secret_iam_member.kong_sa_secret_access,
    google_secret_manager_secret_version.kong_config
  ]

  template {
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    service_account = data.google_service_account.kong_sa.email

    containers {
      image = var.kong_image
      ports {
        container_port = 8000
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      env {
        name  = "KONG_DATABASE"
        value = "off"
      }
      env {
        name  = "KONG_DECLARATIVE_CONFIG"
        value = "/etc/kong/kong.yml"
      }
      env {
        name  = "KONG_PLUGINS"
        value = "bundled,gcp-service-account"
      }
      env {
        name  = "FORCE_REDEPLOY"
        value = google_secret_manager_secret_version.kong_config.version
      }
      volume_mounts {
        name       = "kong-config"
        mount_path = "/etc/kong"
      }
    }

    volumes {
      name = "kong-config"
      secret {
        secret = google_secret_manager_secret.kong_config.secret_id
        items {
          version = "latest"
          path    = "kong.yml"
        }
      }
    }

    # Direct VPC Egress: Mounts Cloud Run inside the Shared VPC directly
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}

# IAM Invoker Binding restricting access to authorized callers only
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.kong_gateway.name
  role     = "roles/run.invoker"
  members = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# ====================================================================
# Internal HTTP Load Balancer & Cloud DNS Integration for esmeralda.internal
# ====================================================================

# 1. Serverless NEG fronting the Kong Gateway Cloud Run service
resource "google_compute_region_network_endpoint_group" "kong_neg" {
  name                  = "neg-kong-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.kong_gateway.name
  }
}

# 2. Regional Internal Backend Service
resource "google_compute_region_backend_service" "kong_backend" {
  name                  = "backend-kong-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.kong_neg.id
  }
}

# 3. Regional Internal URL Map
resource "google_compute_region_url_map" "kong_url_map" {
  name            = "ilb-kong-url-map-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.kong_backend.id
}

# 4. Internal Root CA and Wildcard TLS Certificate for *.esmeralda.internal
resource "tls_private_key" "esmeralda_ca_key" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "esmeralda_ca_cert" {
  private_key_pem   = tls_private_key.esmeralda_ca_key.private_key_pem
  is_ca_certificate = true

  subject {
    common_name  = "Esmeralda Internal Root CA"
    organization = "Esmeralda Internal"
  }

  validity_period_hours = 87600 # 10 years

  allowed_uses = [
    "cert_signing",
    "crl_signing",
    "digital_signature",
    "key_encipherment",
  ]
}

resource "google_secret_manager_secret" "esmeralda_internal_ca" {
  secret_id = "esmeralda-internal-root-ca-${var.environment}"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "esmeralda_internal_ca" {
  secret      = google_secret_manager_secret.esmeralda_internal_ca.id
  secret_data = tls_self_signed_cert.esmeralda_ca_cert.cert_pem
}

resource "tls_private_key" "kong_ilb_key" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "kong_ilb_csr" {
  private_key_pem = tls_private_key.kong_ilb_key.private_key_pem

  subject {
    common_name  = "*.esmeralda.internal"
    organization = "Esmeralda Dev"
  }

  dns_names = [
    "*.esmeralda.internal",
    "esmeralda.internal",
    "legacy-dms.esmeralda.internal",
    "income-verification.esmeralda.internal",
    "corporate-email.esmeralda.internal",
    "a2a-mortgage-agent.esmeralda.internal",
    "root-agent.esmeralda.internal",
  ]
}

resource "tls_locally_signed_cert" "kong_ilb_cert" {
  cert_request_pem   = tls_cert_request.kong_ilb_csr.cert_request_pem
  ca_private_key_pem = tls_private_key.esmeralda_ca_key.private_key_pem
  ca_cert_pem        = tls_self_signed_cert.esmeralda_ca_cert.cert_pem

  validity_period_hours = 8760 # 1 year (complies with Chromium <= 398 days check)

  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

resource "google_compute_region_ssl_certificate" "kong_ilb_cert" {
  name_prefix = "cert-kong-ilb-"
  project     = var.project_id
  region      = var.region
  private_key = tls_private_key.kong_ilb_key.private_key_pem
  certificate = "${tls_locally_signed_cert.kong_ilb_cert.cert_pem}${tls_self_signed_cert.esmeralda_ca_cert.cert_pem}"

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_target_https_proxy" "kong_https_proxy" {
  name             = "ilb-kong-https-proxy-${var.environment}"
  project          = var.project_id
  region           = var.region
  url_map          = google_compute_region_url_map.kong_url_map.id
  ssl_certificates = [google_compute_region_ssl_certificate.kong_ilb_cert.id]
}

# Regional Internal Forwarding Rule for HTTPS (port 443)
resource "google_compute_forwarding_rule" "kong_forwarding_rule" {
  name                  = "ilb-kong-rule-${var.environment}"
  project               = var.project_id
  region                = var.region
  ip_protocol           = "TCP"
  port_range            = "443"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = var.vpc_id
  subnetwork            = var.subnet_id
  target                = google_compute_region_target_https_proxy.kong_https_proxy.id
}

# 5. Cloud DNS A Records in Shared VPC Private Zone mapping esmeralda.internal to ILB VIP
resource "google_dns_record_set" "esmeralda_internal_apex" {
  count        = var.net_host_project_id != "" && var.dns_zone_name != "" ? 1 : 0
  project      = var.net_host_project_id
  managed_zone = var.dns_zone_name
  name         = "esmeralda.internal."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_forwarding_rule.kong_forwarding_rule.ip_address]
}

resource "google_dns_record_set" "esmeralda_internal_wildcard" {
  count        = var.net_host_project_id != "" && var.dns_zone_name != "" ? 1 : 0
  project      = var.net_host_project_id
  managed_zone = var.dns_zone_name
  name         = "*.esmeralda.internal."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_forwarding_rule.kong_forwarding_rule.ip_address]
}
