# --- INPUT VARIABLES CONTRACT ---
variable "project_id" {
  description = "The GCP project ID allocated for gateway ingress (prj-gateway)"
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
  description = "The self-link of the gateway/backend subnetwork"
  type        = string
}

variable "agent_endpoints" {
  description = "A map of logical agent names to their dynamic Vertex AI Reasoning Engine configuration"
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
  }))
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

# --- OUTPUTS CONTRACT ---
output "gateway_ingress_ip" {
  description = "The internal private VIP of the gateway load balancer or proxy endpoint"
  value       = string
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by the gateway (e.g., esmeralda.internal)"
  value       = string
}

variable "routing_broker_image" {
  description = "The container image URL of the esmeralda-routing-broker proxy service"
  type        = string
  default     = "us-central1-docker.pkg.dev/prj-esmeralda-mcps/mcp-repo/routing-broker:latest" # AUDIT-05 Fix
}

