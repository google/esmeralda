# Serverless NEGs
resource "google_compute_region_network_endpoint_group" "dms_neg" {
  name                  = "dms-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = "legacy-dms"
  }
}

resource "google_compute_region_network_endpoint_group" "income_neg" {
  name                  = "income-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = "income-verification-api"
  }
}

resource "google_compute_region_network_endpoint_group" "email_neg" {
  name                  = "email-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = "corporate-email"
  }
}

# Backend Services
resource "google_compute_region_backend_service" "dms_backend" {
  name                  = "dms-backend"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.dms_neg.id
  }
}

resource "google_compute_region_backend_service" "income_backend" {
  name                  = "income-backend"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.income_neg.id
  }
}

resource "google_compute_region_backend_service" "email_backend" {
  name                  = "email-backend"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.email_neg.id
  }
}

# URL Map
resource "google_compute_region_url_map" "default" {
  name            = "internal-tools-url-map"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.dms_backend.id

  host_rule {
    hosts        = ["dms.internal.gateway"]
    path_matcher = "dms-paths"
  }
  
  host_rule {
    hosts        = ["income-verification.internal.gateway"]
    path_matcher = "income-paths"
  }
  
  host_rule {
    hosts        = ["email.internal.gateway"]
    path_matcher = "email-paths"
  }

  path_matcher {
    name            = "dms-paths"
    default_service = google_compute_region_backend_service.dms_backend.id
  }

  path_matcher {
    name            = "income-paths"
    default_service = google_compute_region_backend_service.income_backend.id
  }

  path_matcher {
    name            = "email-paths"
    default_service = google_compute_region_backend_service.email_backend.id
  }
}

# HTTP Proxy (Agent communicates via HTTP on port 80 or 8080)
# To match human readable names natively if we don't configure self-signed certs.
# However, agent.yaml uses HTTPS. If agent uses HTTPS, we'd need an HTTPS proxy.
# Let's provide HTTP and HTTPS proxies, but we'd need a self-managed cert for HTTPS.
# Wait, let's just use HTTP proxy, and update agent.yaml to use http://

resource "google_compute_region_target_http_proxy" "default" {
  name    = "internal-tools-http-proxy"
  project = var.project_id
  region  = var.region
  url_map = google_compute_region_url_map.default.id
}

resource "google_compute_forwarding_rule" "default" {
  name                  = "internal-tools-forwarding-rule"
  project               = var.project_id
  region                = var.region
  network               = var.network_self_link
  subnetwork            = var.subnetwork_self_link
  ip_address            = var.ip_address
  load_balancing_scheme = "INTERNAL_MANAGED"
  port_range            = "80"
  target                = google_compute_region_target_http_proxy.default.id
  network_tier          = "PREMIUM"
}
