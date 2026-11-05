terraform {
  required_version = ">= 1.3"
}

# Envoy bootstrap for the *.esmeralda.internal gateway, built as an object and rendered with yamlencode()
# so the structure is always valid YAML. tests/gateway renders this module and runs the result in a real
# envoy (make test-gateway).
#
# Objects whose shape depends on a condition use jsondecode(cond ? jsonencode(a) : jsonencode(b)):
# Terraform requires both branches of a conditional to have the same type, and strings always do.
locals {
  url_pattern = "^(https?)://([^/:?#]+)(?::([0-9]+))?(/[^?#]*)?$"

  # One entry per target, keyed by logical name, with the endpoint URL split into its parts.
  targets = {
    for ep in values(var.agent_endpoints) : ep.logical_name => {
      name      = ep.logical_name
      scheme    = regex(local.url_pattern, ep.endpoint_url)[0]
      host      = regex(local.url_pattern, ep.endpoint_url)[1]
      port      = coalesce(regex(local.url_pattern, ep.endpoint_url)[2], regex(local.url_pattern, ep.endpoint_url)[0] == "https" ? "443" : "80")
      base_path = trimsuffix(coalesce(regex(local.url_pattern, ep.endpoint_url)[3], "/"), "/")
      streaming = ep.streaming
      # Same rule as Kong's gcp-service-account plugin: Google APIs take an OAuth2 access token,
      # everything else (Cloud Run) an ID token for the audience; no audience means no token.
      token = ep.audience == "" ? "none" : (length(regexall("googleapis\\.com", ep.audience)) > 0 ? "access_token" : "id_token")
      audience_json = (length(regexall("googleapis\\.com", ep.audience)) > 0
        ? jsonencode({ "@type" = "type.googleapis.com/envoy.extensions.filters.http.gcp_authn.v3.Audience", access_token = {} })
      : jsonencode({ "@type" = "type.googleapis.com/envoy.extensions.filters.http.gcp_authn.v3.Audience", url = ep.audience }))
    }
  }

  hostnames = concat([var.apex_domain], [for t in values(local.targets) : "${t.name}.${var.apex_domain}"])

  # Route action to a target, appending the request path to the target's base path.
  # Envoy's default route timeout is 15 s: set it explicitly (none for SSE, where stream_idle_timeout
  # applies). Retries on connection failures match Kong's default of 5 per service.
  target_route = {
    for t in values(local.targets) : t.name => merge(
      {
        cluster           = t.name
        auto_host_rewrite = true
        timeout           = t.streaming ? "0s" : var.route_timeout
        retry_policy      = { retry_on = "connect-failure", num_retries = 5 }
      },
      jsondecode(t.base_path == "" ? "{}" : jsonencode({ prefix_rewrite = "${t.base_path}/" })),
    )
  }

  virtual_hosts = [
    for t in values(local.targets) : {
      name    = t.name
      domains = ["${t.name}.${var.apex_domain}"]
      routes  = [{ match = { prefix = "/" }, route = local.target_route[t.name] }]
    }
  ]

  target_clusters = [
    for t in values(local.targets) : merge(
      {
        name              = t.name
        type              = "LOGICAL_DNS"
        dns_lookup_family = "V4_ONLY"
        connect_timeout   = "5s"
        load_assignment = {
          cluster_name = t.name
          endpoints = [{
            lb_endpoints = [{ endpoint = { address = { socket_address = { address = t.host, port_value = tonumber(t.port) } } } }]
          }]
        }
      },
      jsondecode(t.scheme != "https" ? "{}" : jsonencode({
        transport_socket = {
          name = "envoy.transport_sockets.tls"
          typed_config = {
            "@type" = "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext"
            sni     = t.host
            common_tls_context = {
              validation_context = {
                trusted_ca                    = { filename = var.ca_bundle_path }
                match_typed_subject_alt_names = [{ san_type = "DNS", matcher = { exact = t.host } }]
              }
            }
          }
        }
      })),
      jsondecode(t.token == "none" ? "{}" : jsonencode({
        metadata = { typed_filter_metadata = { "envoy.filters.http.gcp_authn" = jsondecode(t.audience_json) } }
      })),
    )
  ]

  # Token source for gcp_authn: the metadata server of the Cloud Run instance.
  metadata_cluster = {
    name              = "gcp_metadata"
    type              = "STRICT_DNS"
    dns_lookup_family = "V4_ONLY"
    connect_timeout   = "2s"
    load_assignment = {
      cluster_name = "gcp_metadata"
      endpoints = [{
        lb_endpoints = [{ endpoint = { address = { socket_address = { address = var.metadata_host, port_value = var.metadata_port } } } }]
      }]
    }
  }

  access_log = {
    name = "envoy.access_loggers.stdout"
    typed_config = {
      "@type" = "type.googleapis.com/envoy.extensions.access_loggers.stream.v3.StdoutAccessLog"
      log_format = {
        json_format = {
          severity         = "INFO"
          method           = "%REQ(:METHOD)%"
          authority        = "%REQ(:AUTHORITY)%"
          path             = "%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%"
          status           = "%RESPONSE_CODE%"
          response_flags   = "%RESPONSE_FLAGS%"
          upstream_cluster = "%UPSTREAM_CLUSTER%"
          duration_ms      = "%DURATION%"
          api_key          = "%REQ(X-API-KEY)%"
          traceparent      = "%REQ(TRACEPARENT)%"
          request_id       = "%REQ(X-REQUEST-ID)%"
        }
      }
    }
  }

  # Replaces the Authorization header with a token for the cluster's audience (cached until expiry).
  gcp_authn = {
    name = "envoy.filters.http.gcp_authn"
    typed_config = {
      "@type"      = "type.googleapis.com/envoy.extensions.filters.http.gcp_authn.v3.GcpAuthnFilterConfig"
      cluster      = "gcp_metadata"
      timeout      = "5s"
      cache_config = { cache_size = 64 }
    }
  }

  router = {
    name = "envoy.filters.http.router"
    typed_config = {
      "@type"                = "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"
      suppress_envoy_headers = true
    }
  }

  envoy_config = {
    admin = {
      address = { socket_address = { address = "127.0.0.1", port_value = var.admin_port } }
    }
    static_resources = {
      listeners = [{
        name    = "ingress"
        address = { socket_address = { address = var.listener_address, port_value = var.listener_port } }
        filter_chains = [{
          filters = [{
            name = "envoy.filters.network.http_connection_manager"
            typed_config = {
              "@type"             = "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager"
              stat_prefix         = "ingress"
              strip_any_host_port = true
              # Resolve dot segments and repeated slashes before routing, so no request climbs out of a
              # target's base path; reject encoded slashes (%2F, %5C) rather than guess what they mean.
              normalize_path                   = true
              merge_slashes                    = true
              path_with_escaped_slashes_action = "REJECT_REQUEST"
              strip_trailing_host_dot          = true
              # Close streams that stay idle for longer than this.
              stream_idle_timeout = var.stream_idle_timeout
              access_log          = [local.access_log]
              route_config        = { name = "gateway", virtual_hosts = local.virtual_hosts }
              http_filters        = [local.gcp_authn, local.router]
            }
          }]
        }]
      }]
      clusters = concat(local.target_clusters, [local.metadata_cluster])
    }
  }
}
