# --- INPUT VARIABLES CONTRACT ---
# Bring-your-own gateway adapter on Cloud Run: takes the gateway service account and per-target routing config.

variable "project_id" {
  description = "The GCP project ID allocated for gateway ingress (esm-<env>-gateway-<sfx>)"
  type        = string
}

variable "region" {
  description = "The region where gateway workloads are deployed (the entry point's region)"
  type        = string
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network (Envoy's Direct VPC egress)"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the subnetwork used for Envoy's Direct VPC egress"
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
    # How `make test-gateway` checks the route: "mcp" (MCP initialize, POST /mcp), "a2a" (agent card,
    # GET /v1/card), "query" (a real agent query) or "none" (TLS + routing only, any HTTP answer).
    probe = optional(string, "none")
  }))
  default = {}

  validation {
    condition     = alltrue([for e in values(var.agent_endpoints) : contains(["mcp", "a2a", "query", "none"], e.probe)])
    error_message = "agent_endpoints[*].probe must be one of: mcp, a2a, query, none."
  }
}

variable "apex_domain" {
  description = "Private DNS zone the gateway serves (the entry point domain)"
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
  description = "Identities allowed to call the gateway (roles/run.invoker): service account emails or full members (serviceAccount:, principal://, principalSet://), e.g. a calling agent's Agent Identity principal"
  type        = list(string)
  default     = []
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
