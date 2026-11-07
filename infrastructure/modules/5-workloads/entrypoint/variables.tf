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

variable "catalog" {
  description = "Service catalog (env.yaml catalog): name => kind (mcp | a2a | adk), token callers send when served by direct (id | access) and make test-gateway probe (mcp | a2a | query | none). Other attributes (unit, callers, timeout) are ignored here"
  type = map(object({
    kind  = string
    token = string
    probe = optional(string, "none")
  }))

  validation {
    condition     = length(var.catalog) > 0
    error_message = "The service catalog (catalog in env.yaml) is empty: the entry point has nothing to route."
  }

  validation {
    condition = alltrue([
      for name, entry in var.catalog :
      contains(["mcp", "a2a", "adk"], entry.kind) && contains(["id", "access"], entry.token) && contains(["mcp", "a2a", "query", "none"], entry.probe)
    ])
    error_message = "catalog entries need kind = mcp | a2a | adk, token = id | access and probe = mcp | a2a | query | none."
  }

  validation {
    condition     = alltrue([for name in keys(var.catalog) : can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name))])
    error_message = "catalog names become host labels (<name>.<domain>): lowercase letters, digits and hyphens."
  }
}

variable "direct_hosts" {
  description = "Built-in direct backends (gateways/direct output hosts): catalog name => backend service, optional host rewrite, route timeout and path prefix rewrites"
  type = map(object({
    backend_service_id = string
    host_rewrite       = optional(string)
    timeout_sec        = optional(number)
    path_rewrites = optional(list(object({
      match   = string # prefix | full
      path    = string
      rewrite = string
    })), [])
  }))
  default = {}

  validation {
    condition     = alltrue(flatten([for name, host in var.direct_hosts : [for rule in host.path_rewrites : contains(["prefix", "full"], rule.match)]]))
    error_message = "direct_hosts[*].path_rewrites[*].match must be prefix or full."
  }
}

variable "direct_default_backend_service_id" {
  description = "Backend for hosts outside the catalog when gateways.default is direct (gateways/direct output default_backend_service_id)"
  type        = string
  default     = null
}

variable "adapters" {
  description = "Enabled bring-your-own gateways (gateways.enabled): name => regional INTERNAL_MANAGED backend service ID (each adapter module's backend_service_id output)"
  type        = map(string)
  default     = {}

  validation {
    condition     = !contains(keys(var.adapters), "direct")
    error_message = "direct is built in: don't list it in gateways.enabled (that list holds bring-your-own adapters only)."
  }

  validation {
    condition     = alltrue([for name, id in var.adapters : id != null && id != ""])
    error_message = "Every enabled adapter must output a non-empty backend_service_id. A null value means that adapter module is still a placeholder (e.g. the apigee stub)."
  }
}

variable "default_backend" {
  description = "Backend of every catalog host not listed in host_rules: direct (built-in) or an enabled adapter"
  type        = string
  default     = "direct"

  validation {
    condition     = var.default_backend == "direct" || contains(keys(var.adapters), var.default_backend)
    error_message = "gateways.default in env.yaml must be direct or one of gateways.enabled."
  }
}

variable "host_rules" {
  description = "Optional per-host overrides: host label (e.g. legacy-dms, routed as legacy-dms.<domain>) or FQDN => direct or an enabled adapter. Lets backends run side by side and move host by host"
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for host, backend in var.host_rules : backend == "direct" || contains(keys(var.adapters), backend)])
    error_message = "Every value of gateways.host_rules in env.yaml must be direct or one of gateways.enabled."
  }
}
