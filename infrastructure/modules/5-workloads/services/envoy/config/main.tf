terraform {
  required_version = ">= 1.3"
}

# Envoy bootstrap for the *.esmeralda.internal gateway, built as an object and rendered with yamlencode()
# so the structure is always valid YAML. tests/gateway renders this module and runs the result in a real
# envoy (make test-gateway).
locals {
  hostnames = concat([var.apex_domain], [for ep in values(var.agent_endpoints) : "${ep.logical_name}.${var.apex_domain}"])

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
              stream_idle_timeout = var.stream_idle_timeout
              access_log          = [local.access_log]
              route_config        = { name = "gateway", virtual_hosts = [] }
              http_filters        = [local.router]
            }
          }]
        }]
      }]
      clusters = []
    }
  }
}
