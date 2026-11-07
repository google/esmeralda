# --- INPUT VARIABLES CONTRACT ---
variable "project_id" {
  description = "The GCP project ID hosting the internal entry point (the gateway project)"
  type        = string
}

variable "region" {
  description = "The region of the regional internal Application Load Balancer"
  type        = string
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the subnetwork the entry point VIP is reserved in (layer 2; not the proxy-only subnet)"
  type        = string
}

variable "ip_address" {
  description = "Reserved internal IP (layer 2 output entrypoint_address) the forwarding rule listens on"
  type        = string
}

variable "ssl_certificate_id" {
  description = "Regional SSL certificate for <domain> + *.<domain> (layer 3 output entrypoint_ssl_certificate_id)"
  type        = string
}

variable "domain" {
  description = "The private domain served by the entry point, without trailing dot (layer 2 output entrypoint_domain)"
  type        = string
}

variable "backends" {
  description = "Enabled gateways: name => regional INTERNAL_MANAGED backend service ID (each gateway module's backend_service_id output)"
  type        = map(string)

  validation {
    condition     = length(var.backends) > 0
    error_message = "At least one gateway backend is required: list it in gateways.enabled in env.yaml and deploy its unit under layer-5-workloads/gateways/."
  }

  validation {
    condition     = alltrue([for name, id in var.backends : id != null && id != ""])
    error_message = "Every enabled gateway must output a non-empty backend_service_id. A null value means that gateway module is still a placeholder (e.g. the apigee stub)."
  }
}

variable "default_backend" {
  description = "Gateway (key of backends) that receives every host not listed in host_rules"
  type        = string

  validation {
    condition     = contains(keys(var.backends), var.default_backend)
    error_message = "gateways.default in env.yaml must be one of gateways.enabled (the backends passed to the entrypoint)."
  }
}

variable "host_rules" {
  description = "Optional per-host overrides: host label (e.g. legacy-dms, routed as legacy-dms.<domain>) or FQDN => gateway name. Lets two gateways run side by side and move host by host"
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for host, backend in var.host_rules : contains(keys(var.backends), backend)])
    error_message = "Every value of gateways.host_rules in env.yaml must be one of gateways.enabled (the backends passed to the entrypoint)."
  }
}
