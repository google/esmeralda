# ====================================================================
# Internal entry point for <domain> and *.<domain>
# --------------------------------------------------------------------
# Regional internal Application Load Balancer that sends every private hostname to one of the
# enabled gateways (modules/5-workloads/gateways/*). The pieces that must outlive the gateways
# live in lower layers:
#   - layer 2: the reserved internal IP and the <domain> / *.<domain> A records
#   - layer 3: the <domain> + *.<domain> certificate, signed by the internal Root CA
# This unit only holds the routing, so it is applied after the gateways and changes whenever
# a gateway is added, swapped, or a host is moved between gateways.
# ====================================================================

locals {
  # Host label => FQDN ("legacy-dms" => "legacy-dms.<domain>"); FQDNs are kept as they are.
  host_fqdns = {
    for host, backend in var.host_rules :
    host => (endswith(host, ".${var.domain}") || host == var.domain) ? host : "${host}.${var.domain}"
  }

  # One host_rule + path_matcher per gateway referenced in host_rules.
  routed_backends = toset(values(var.host_rules))
}

resource "google_compute_region_url_map" "entrypoint" {
  name            = "ilb-esmeralda-url-map-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = var.backends[var.default_backend]

  dynamic "host_rule" {
    for_each = local.routed_backends
    content {
      hosts        = sort([for host, backend in var.host_rules : local.host_fqdns[host] if backend == host_rule.value])
      path_matcher = "gw-${host_rule.value}"
    }
  }

  dynamic "path_matcher" {
    for_each = local.routed_backends
    content {
      name            = "gw-${path_matcher.value}"
      default_service = var.backends[path_matcher.value]
    }
  }
}

resource "google_compute_region_target_https_proxy" "entrypoint" {
  name             = "ilb-esmeralda-https-proxy-${var.environment}"
  project          = var.project_id
  region           = var.region
  url_map          = google_compute_region_url_map.entrypoint.id
  ssl_certificates = [var.ssl_certificate_id]
}

# HTTPS (443) on the layer-2 reserved IP. Uses the region's proxy-only subnet (layer 2).
resource "google_compute_forwarding_rule" "entrypoint" {
  name                  = "ilb-esmeralda-rule-${var.environment}"
  project               = var.project_id
  region                = var.region
  ip_protocol           = "TCP"
  port_range            = "443"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = var.vpc_id
  subnetwork            = var.subnet_id
  ip_address            = var.ip_address
  target                = google_compute_region_target_https_proxy.entrypoint.id
}
