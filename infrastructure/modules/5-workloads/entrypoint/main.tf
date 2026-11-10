# ====================================================================
# Internal entry point for <domain> and *.<domain>
# --------------------------------------------------------------------
# Regional internal Application Load Balancer that serves every host of the service catalog
# (env.yaml `catalog`) as <name>.<domain>, through one of:
#   - direct (built-in, the default): per-host backends from modules/5-workloads/gateways/direct,
#     no proxy; callers send their own credential (ID token to Cloud Run, access token to Agent
#     Runtime);
#   - an adapter (bring-your-own gateway under modules/5-workloads/gateways/<name>, e.g. envoy):
#     one backend service for every host sent to it.
# The pieces that must outlive the backends live in lower layers:
#   - layer 2: the reserved internal IP and the <domain> / *.<domain> A records
#   - layer 3: the <domain> + *.<domain> certificate, signed by the internal Root CA
# This unit only holds the routing, so it is applied after the backends and changes whenever a
# catalog entry, an adapter or a host's backend (host_rules) changes.
# ====================================================================

locals {
  # host_rules keys: host label ("legacy-dms") or FQDN ("legacy-dms.<domain>"); normalized to labels.
  # The apex (<domain>) is kept as it is.
  rules = {
    for host, backend in var.host_rules :
    (endswith(host, ".${var.domain}") ? trimsuffix(host, ".${var.domain}") : host) => backend
  }
  fqdn = { for label, backend in local.rules : label => (label == var.domain ? label : "${label}.${var.domain}") }

  # Effective backend of every catalog host: its host rule, else the default.
  routing = { for name, entry in var.catalog : name => lookup(local.rules, name, var.default_backend) }

  # direct: one host rule + path matcher per host.
  direct_routes = { for name, backend in local.routing : name => var.direct_hosts[name] if backend == "direct" }

  # Adapters: one host rule + path matcher per adapter named in host_rules (hosts outside the catalog,
  # e.g. the apex, may be sent to an adapter too). Catalog hosts that only follow the default need no rule.
  adapter_rules   = { for label, backend in local.rules : label => backend if backend != "direct" }
  routed_adapters = toset(values(local.adapter_rules))

  default_service = var.default_backend == "direct" ? var.direct_default_backend_service_id : var.adapters[var.default_backend]
}

resource "google_compute_region_url_map" "entrypoint" {
  name            = "ilb-esmeralda-url-map-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = local.default_service

  # --- direct: one host per path matcher ---
  dynamic "host_rule" {
    for_each = local.direct_routes
    content {
      hosts        = ["${host_rule.key}.${var.domain}"]
      path_matcher = "direct-${host_rule.key}"
    }
  }

  dynamic "path_matcher" {
    for_each = local.direct_routes
    content {
      name            = "direct-${path_matcher.key}"
      default_service = path_matcher.value.backend_service_id

      # Agent hosts: rewrite host + path prefix to the engine. The route timeout bounds the whole
      # response; the psc aiplatform backend also caps the time to first byte at 30 s (see gateways/direct).
      dynamic "route_rules" {
        for_each = path_matcher.value.path_rewrites
        content {
          priority = route_rules.key + 1
          service  = path_matcher.value.backend_service_id
          match_rules {
            prefix_match    = route_rules.value.match == "prefix" ? route_rules.value.path : null
            full_path_match = route_rules.value.match == "full" ? route_rules.value.path : null
          }
          route_action {
            url_rewrite {
              host_rewrite        = path_matcher.value.host_rewrite
              path_prefix_rewrite = route_rules.value.rewrite
            }
            dynamic "timeout" {
              for_each = path_matcher.value.timeout_sec != null ? [path_matcher.value.timeout_sec] : []
              content {
                seconds = tostring(timeout.value)
              }
            }
          }
        }
      }
    }
  }

  # --- adapters: one path matcher per adapter named in host_rules ---
  dynamic "host_rule" {
    for_each = local.routed_adapters
    content {
      hosts        = sort([for label, backend in local.adapter_rules : local.fqdn[label] if backend == host_rule.value])
      path_matcher = "gw-${host_rule.value}"
    }
  }

  dynamic "path_matcher" {
    for_each = local.routed_adapters
    content {
      name            = "gw-${path_matcher.value}"
      default_service = var.adapters[path_matcher.value]
    }
  }

  lifecycle {
    precondition {
      condition     = alltrue([for name, backend in local.routing : backend != "direct" || contains(keys(var.direct_hosts), name)])
      error_message = "Every catalog host served by direct needs a direct backend: check that the gateways/direct unit maps its catalog unit."
    }
    precondition {
      condition     = alltrue([for label, backend in local.rules : backend != "direct" || contains(keys(var.catalog), label)])
      error_message = "gateways.host_rules can only send catalog hosts to direct (direct has no backend for hosts outside the catalog)."
    }
    precondition {
      condition     = local.default_service != null && local.default_service != ""
      error_message = "gateways.default = \"direct\" needs at least one catalog entry served by the gateways/direct unit."
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
