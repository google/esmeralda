# --- INPUT VARIABLES CONTRACT ---
# Selected with gateway_product in env.yaml (the other option is services/apigee). Same inputs the Kong
# module took before #73, plus the gateway service account and optional path_map/streaming per target.

variable "project_id" {
  description = "The GCP project ID allocated for gateway ingress (esm-<env>-gateway-<sfx>)"
  type        = string
}

variable "region" {
  description = "The region where gateway workloads are deployed"
  type        = string
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the gateway/backend subnetwork (Cloud Run Direct VPC egress and the LB VIP)"
  type        = string
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, prd)"
  type        = string
  default     = "dev"
}

variable "agent_endpoints" {
  description = "Targets served under <logical_name>.<apex_domain>; see the config submodule for the attribute contract"
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
  description = "Private DNS zone the gateway serves"
  type        = string
  default     = "esmeralda.internal"
}

variable "gateway_image" {
  description = "Envoy image (>= 1.39 for gcp_authn access tokens). Artifact Registry tags are resolved to digests"
  type        = string
}

variable "gateway_sa_email" {
  description = "Service account Envoy runs as; it is the identity upstreams see (invoker on MCPs, aiplatform.user on agents)"
  type        = string
}

variable "invoker_service_accounts" {
  description = "Service accounts allowed to call the gateway (roles/run.invoker)"
  type        = list(string)
  default     = []
}

variable "net_host_project_id" {
  description = "The project ID of the Shared VPC host that owns the DNS zone (defaults to project_id)"
  type        = string
  default     = ""
}

variable "dns_zone_name" {
  description = "The name of the Cloud DNS private zone for apex_domain; empty skips the DNS records"
  type        = string
  default     = ""
}

variable "min_instances" {
  description = "Minimum number of warm instances"
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Maximum number of instances"
  type        = number
  default     = 10
}

variable "cpu_limit" {
  description = "CPU limit of the Envoy container"
  type        = string
  default     = "1"
}

variable "memory_limit" {
  description = "Memory limit of the Envoy container"
  type        = string
  default     = "512Mi"
}

variable "request_timeout" {
  description = "Cloud Run request timeout; long enough for SSE streams (the default 300s would cut them)"
  type        = string
  default     = "3600s"
}

variable "rate_limit_per_minute" {
  description = "Requests per minute per X-API-Key value, per instance"
  type        = number
  default     = 120
}

variable "internal_ca_cert_pem" {
  description = "PEM of the internal Root CA (layer 3 security) that signs the gateway's LB leaf"
  type        = string
}

variable "internal_ca_key_pem" {
  description = "Private key of the internal Root CA (layer 3 security)"
  type        = string
  sensitive   = true
}
