# --- INPUT VARIABLES CONTRACT ---
# Pure module: no providers, no resources. Renders the Envoy bootstrap that the services/envoy module
# stores in Secret Manager, so tests/gateway can render and run exactly what production runs.

variable "agent_endpoints" {
  description = <<-EOT
    Targets served under <logical_name>.<apex_domain>, same contract as the Kong module:
      endpoint_url: upstream base URL, scheme://host[:port][/base/path], no query string
      audience:     contains "googleapis.com" => OAuth2 access token; "" => no token; else ID token for it
      path_map:     optional exact request path => suffix appended to the base path (may carry a query)
      streaming:    optional; SSE routes get no route timeout (stream_idle_timeout still applies)
  EOT
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
    audience     = string
    path_map     = optional(map(string), {})
    streaming    = optional(bool, false)
  }))
  default = {}
}

variable "apex_domain" {
  description = "Private DNS zone the gateway serves; each target is <logical_name>.<apex_domain>"
  type        = string
  default     = "esmeralda.internal"
}

variable "listener_port" {
  description = "Port Envoy listens on (the Cloud Run container port)"
  type        = number
  default     = 8080
}

variable "listener_address" {
  description = "Address Envoy listens on: all interfaces on Cloud Run; tests use 127.0.0.1"
  type        = string
  default     = "0.0.0.0"
}

variable "admin_port" {
  description = "Port of the Envoy admin interface, bound to 127.0.0.1 only"
  type        = number
  default     = 9901
}

variable "metadata_host" {
  description = "Metadata server gcp_authn fetches tokens from"
  type        = string
  default     = "metadata.google.internal"
}

variable "metadata_port" {
  description = "Port of the metadata server"
  type        = number
  default     = 80
}

variable "ca_bundle_path" {
  description = "CA bundle Envoy uses to verify upstream TLS certificates"
  type        = string
  default     = "/etc/ssl/certs/ca-certificates.crt"
}

variable "rate_limit_per_minute" {
  description = "Requests per minute per X-API-Key value, per Envoy instance (Kong parity: 120)"
  type        = number
  default     = 120
}

variable "route_timeout" {
  description = "Timeout of non-streaming routes; must exceed the clients' timeouts (A2A 60 s, MCP 30 s)"
  type        = string
  default     = "120s"
}

variable "stream_idle_timeout" {
  description = "Idle timeout of a request or response stream (applies to SSE routes too)"
  type        = string
  default     = "300s"
}
