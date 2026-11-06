terraform {
  required_version = ">= 1.3"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0"
    }
  }
}

# Envoy gateway behind the internal HTTPS LB for *.esmeralda.internal (replaces services/kong).
# The Envoy bootstrap comes from ./config, which tests/gateway renders and runs in a real envoy.
module "config" {
  source = "./config"

  agent_endpoints       = var.agent_endpoints
  apex_domain           = var.apex_domain
  rate_limit_per_minute = var.rate_limit_per_minute
}

locals {
  # Cloud Run accepts ID tokens for these audiences, so callers can mint tokens for the hostname they call.
  custom_audiences = flatten([for host in module.config.hostnames : ["https://${host}", "http://${host}", host]])
}

# The Envoy bootstrap, stored in Secret Manager and mounted into the container.
resource "google_secret_manager_secret" "config" {
  secret_id = "envoy-config-${var.environment}"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "config" {
  secret      = google_secret_manager_secret.config.id
  secret_data = module.config.envoy_yaml

  # Each revision mounts the exact version it was deployed with. Create the new version before the service
  # moves to it, and keep old versions readable (ABANDON leaves them in Secret Manager): earlier revisions
  # can still start instances, and traffic can be rolled back to them. Prune old versions out of band.
  deletion_policy = "ABANDON"
  lifecycle {
    create_before_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "gateway_sa_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.gateway_sa_email}"
}

# Envoy on Cloud Run with internal-only ingress. Mounting the exact secret version makes every config
# change roll out as a new revision.
resource "google_cloud_run_v2_service" "gateway" {
  name                = "envoy-gateway-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false
  custom_audiences    = local.custom_audiences

  template {
    service_account = var.gateway_sa_email
    timeout         = var.request_timeout

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image_by_digest
      args  = ["-c", "/etc/envoy/envoy.yaml", "--disable-hot-restart", "--log-level", "info"]
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      volume_mounts {
        name       = "envoy-config"
        mount_path = "/etc/envoy"
      }
    }

    volumes {
      name = "envoy-config"
      secret {
        secret = google_secret_manager_secret.config.secret_id
        items {
          version = google_secret_manager_secret_version.config.version
          path    = "envoy.yaml"
        }
      }
    }

    # Direct VPC egress for all traffic, so calls to internal-ingress Cloud Run upstreams count as internal.
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.gateway_sa_secret_access]
}

resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  members  = [for sa in var.invoker_service_accounts : "serviceAccount:${sa}"]
}

# ====================================================================
# Internal HTTPS load balancer and DNS for the apex domain
# ====================================================================

resource "google_compute_region_network_endpoint_group" "gateway" {
  name                  = "neg-envoy-gateway-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.gateway.name
  }
}

resource "google_compute_region_backend_service" "gateway" {
  name                  = "backend-envoy-gateway-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.gateway.id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_region_url_map" "gateway" {
  name            = "ilb-envoy-gateway-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.gateway.id
}

# Leaf certificate for every served hostname, signed by the internal Root CA from layer 3 (the CA the
# Agent Gateway TrustConfig and the agents trust).
resource "tls_private_key" "gateway" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "gateway" {
  private_key_pem = tls_private_key.gateway.private_key_pem

  subject {
    common_name  = "*.${var.apex_domain}"
    organization = "Esmeralda ${var.environment}"
  }

  dns_names = concat(["*.${var.apex_domain}"], module.config.hostnames)
}

resource "tls_locally_signed_cert" "gateway" {
  cert_request_pem   = tls_cert_request.gateway.cert_request_pem
  ca_private_key_pem = var.internal_ca_key_pem
  ca_cert_pem        = var.internal_ca_cert_pem

  validity_period_hours = 8760 # 1 year (within Chromium's 398-day limit)
  early_renewal_hours   = 720  # any apply in the last 30 days re-issues it, before it expires

  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

resource "google_compute_region_ssl_certificate" "gateway" {
  name_prefix = "cert-envoy-gateway-"
  project     = var.project_id
  region      = var.region
  private_key = tls_private_key.gateway.private_key_pem
  certificate = "${tls_locally_signed_cert.gateway.cert_pem}${var.internal_ca_cert_pem}"

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_target_https_proxy" "gateway" {
  name             = "ilb-envoy-gateway-https-proxy-${var.environment}"
  project          = var.project_id
  region           = var.region
  url_map          = google_compute_region_url_map.gateway.id
  ssl_certificates = [google_compute_region_ssl_certificate.gateway.id]
}

# Static VIP, so recreating the forwarding rule never changes the address the DNS records point to.
resource "google_compute_address" "gateway" {
  name         = "ip-envoy-gateway-${var.environment}"
  project      = var.project_id
  region       = var.region
  subnetwork   = var.subnet_id
  address_type = "INTERNAL"
}

resource "google_compute_forwarding_rule" "gateway" {
  name                  = "ilb-envoy-gateway-rule-${var.environment}"
  project               = var.project_id
  region                = var.region
  ip_protocol           = "TCP"
  ip_address            = google_compute_address.gateway.address
  port_range            = "443"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = var.vpc_id
  subnetwork            = var.subnet_id
  target                = google_compute_region_target_https_proxy.gateway.id
}

# DNS records go in the Shared VPC host's private zone (or the gateway project's, if no host is given).
# count depends only on the zone name, so it is known at plan time even when project IDs are not.
resource "google_dns_record_set" "apex" {
  count        = var.dns_zone_name != "" ? 1 : 0
  project      = var.net_host_project_id != "" ? var.net_host_project_id : var.project_id
  managed_zone = var.dns_zone_name
  name         = "${var.apex_domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_forwarding_rule.gateway.ip_address]
}

resource "google_dns_record_set" "wildcard" {
  count        = var.dns_zone_name != "" ? 1 : 0
  project      = var.net_host_project_id != "" ? var.net_host_project_id : var.project_id
  managed_zone = var.dns_zone_name
  name         = "*.${var.apex_domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_forwarding_rule.gateway.ip_address]
}
