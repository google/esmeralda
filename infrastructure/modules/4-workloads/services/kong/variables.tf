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
  description = "A map of logical agent names to their dynamic endpoints and routing configuration"
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
    audience     = string
  }))
  default     = {}
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "kong_image" {
  description = "The Container Image URL of Kong Gateway"
  type        = string
  default     = "kong:latest"
}

variable "invoker_service_accounts" {
  description = "A list of service accounts allowed to invoke the Kong gateway"
  type        = list(string)
  default     = []
}

variable "net_host_project_id" {
  description = "The project ID of the Shared VPC Host for DNS record management"
  type        = string
  default     = ""
}

variable "dns_zone_name" {
  description = "The name of the Cloud DNS private zone"
  type        = string
  default     = ""
}
