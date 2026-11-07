terraform {
  required_version = ">= 1.3"
}

# Envoy bootstrap for the *.esmeralda.internal gateway, built as an object and rendered with yamlencode()
# so the structure is always valid YAML. tests/gateway renders this module and runs the result in a real
# envoy (make test-gateway-unit).
#
# Objects whose shape depends on a condition use jsondecode(cond ? jsonencode(a) : jsonencode(b)):
# Terraform requires both branches of a conditional to have the same type, and strings always do.
locals {
  url_pattern   = "^(https?)://([^/:?#]+)(?::([0-9]+))?(/[^?#]*)?$" # same as the validation in variables.tf
  audience_type = "type.googleapis.com/envoy.extensions.filters.http.gcp_authn.v3.Audience"

  # Each endpoint URL split once: [scheme, host, port, base path].
  parsed = {
    for ep in values(var.agent_endpoints) : ep.logical_name => {
      ep         = ep
      parts      = regex(local.url_pattern, ep.endpoint_url)
      google_api = length(regexall("googleapis\\.com", ep.audience)) > 0
    }
  }

  # One entry per target, keyed by logical name.
  targets = {
    for name, p in local.parsed : name => {
      name      = name
      scheme    = p.parts[0]
      host      = p.parts[1]
      port      = coalesce(p.parts[2], p.parts[0] == "https" ? "443" : "80")
      base_path = trimsuffix(coalesce(p.parts[3], "/"), "/")
      streaming = p.ep.streaming
      path_map  = p.ep.path_map
      # Same rule as Kong's gcp-service-account plugin had: Google APIs take an OAuth2 access token,
      # everything else (Cloud Run) an ID token for the audience; no audience means no token.
      token = p.ep.audience == "" ? "none" : (p.google_api ? "access_token" : "id_token")
      audience_json = (p.google_api
        ? jsonencode({ "@type" = local.audience_type, access_token = {} })
      : jsonencode({ "@type" = local.audience_type, url = p.ep.audience }))
    }
  }

  hostnames = concat([var.apex_domain], [for t in values(local.targets) : "${t.name}.${var.apex_domain}"])

  # Route action to a target, appending the request path to the target's base path.
  # Envoy's default route timeout is 15 s: set it explicitly (none for SSE, where stream_idle_timeout
  # applies). Retries on connection failures match Kong's default of 5 per service (before #73).
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

  # A target with a path_map serves only those exact paths (e.g. the orchestrator's
  # /api/stream_reasoning_engine -> <engine>:streamQuery?alt=sse); others serve every path under "/".
  # Mapped paths bring their own query, and Envoy would append the caller's after it (?alt=sse?x=1),
  # so a request with a query string on a mapped path gets a 400 instead.
  has_query      = { name = ":path", string_match = { contains = "?" } }
  query_rejected = { status = 400, body = { inline_string = "query strings are not supported on this path\n" } }
  target_virtual_hosts = [
    for t in values(local.targets) : {
      name    = t.name
      domains = ["${t.name}.${var.apex_domain}"]
      routes = concat(
        [for p in(length(t.path_map) == 0 ? ["/"] : []) : { match = { prefix = p }, route = local.target_route[t.name] }],
        flatten([for k, v in t.path_map : [
          { match = { path = k, headers = [local.has_query] }, direct_response = local.query_rejected },
          { match = { path = k }, route = merge(local.target_route[t.name], { prefix_rewrite = "${t.base_path}${v}" }) },
        ]]),
      )
    }
  ]

  # Path routes on the apex, as under Kong: https://<apex>/<name>/... and /agents/<name>/..., prefix stripped.
  apex_routes = flatten([
    for t in values(local.targets) : [
      [
        for prefix in(length(t.path_map) == 0 ? ["/agents/${t.name}", "/${t.name}"] : []) : [
          { match = { path = prefix }, route = merge(local.target_route[t.name], { prefix_rewrite = "${t.base_path}/" }) },
          { match = { prefix = "${prefix}/" }, route = merge(local.target_route[t.name], { prefix_rewrite = "${t.base_path}/" }) },
        ]
      ],
      [
        for k, v in t.path_map : [
          for prefix in ["/agents/${t.name}", "/${t.name}"] : [
            { match = { path = "${prefix}${k}", headers = [local.has_query] }, direct_response = local.query_rejected },
            { match = { path = "${prefix}${k}" }, route = merge(local.target_route[t.name], { prefix_rewrite = "${t.base_path}${v}" }) },
          ]
        ]
      ],
    ]
  ])

  apex_virtual_host = {
    name    = "apex"
    domains = [var.apex_domain]
    routes = concat(
      [{ match = { path = "/healthz" }, direct_response = { status = 200, body = { inline_string = "ok" } } }],
      local.apex_routes,
    )
  }

  virtual_hosts = concat(local.target_virtual_hosts, [local.apex_virtual_host])

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
          authority        = "%REQ(X-ENVOY-ORIGINAL-HOST?:AUTHORITY)%"
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

  # Rate limiting as under Kong: N requests per minute per X-API-Key value, per instance. A descriptor
  # entry with a key and no value is a wildcard (one bucket per header value). Requests without the
  # header share the default bucket. filter_enabled/filter_enforced default to 0%, hence 100 here.
  rate_limit_bucket = { max_tokens = var.rate_limit_per_minute, tokens_per_fill = var.rate_limit_per_minute, fill_interval = "60s" }
  rate_limit = {
    name = "envoy.filters.http.local_ratelimit"
    typed_config = {
      "@type"                             = "type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit"
      stat_prefix                         = "per_api_key"
      filter_enabled                      = { default_value = { numerator = 100, denominator = "HUNDRED" } }
      filter_enforced                     = { default_value = { numerator = 100, denominator = "HUNDRED" } }
      token_bucket                        = local.rate_limit_bucket
      always_consume_default_token_bucket = false
      rate_limits                         = [{ actions = [{ request_headers = { header_name = "x-api-key", descriptor_key = "api_key" } }] }]
      descriptors                         = [{ entries = [{ key = "api_key" }], token_bucket = local.rate_limit_bucket }]
      enable_retry_after_header           = true
      # Per-key buckets are kept in an LRU (20 by default): beyond that, active keys would reset each other.
      max_dynamic_descriptors = 1000
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
      "@type" = "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"
      # false: rewrites keep the caller's host and path in x-envoy-original-host/-path, which the access
      # log reads (caller-sent copies are dropped in route_config).
      suppress_envoy_headers = false
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
              route_config = {
                name          = "gateway"
                virtual_hosts = local.virtual_hosts
                # Only Envoy writes these: callers can't forge what the access log records, and callers
                # don't see how long the upstream took.
                request_headers_to_remove  = ["x-envoy-original-path", "x-envoy-original-host"]
                response_headers_to_remove = ["x-envoy-upstream-service-time"]
              }
              http_filters = [local.rate_limit, local.gcp_authn, local.router]
            }
          }]
        }]
      }]
      clusters = concat(local.target_clusters, [local.metadata_cluster])
    }
  }
}
