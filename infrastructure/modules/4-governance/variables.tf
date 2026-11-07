# Variables for Layer 4 Governance Stack
variable "governance_project_id" {
  type        = string
  description = "Central Governance GCP Project ID hosting metrics scope, BigQuery logs, and alerts"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Target deployment environment (dev, staging, prod)"
}

variable "spoke_project_ids" {
  type        = list(string)
  default     = []
  description = "List of spoke GCP project IDs to attach to central Governance Metrics Scope"
}

variable "alert_email_address" {
  type        = string
  default     = "esmeralda-secops@google.com"
  description = "Email address for Security & SRE notification channel"
}

variable "runaway_loop_token_threshold" {
  type        = number
  default     = 50000
  description = "Single-request token threshold cap triggering runaway loop alert"
}

# env.yaml's monitoring_config. Only error_rate_threshold_pct is wired so far (the ingress gateway 5xx
# alert); the other attributes there are dropped on type conversion until something uses them.
variable "monitoring_config" {
  type = object({
    error_rate_threshold_pct = optional(number, 2.0)
  })
  default     = {}
  nullable    = false
  description = "Per-environment monitoring settings (env.yaml monitoring_config)"

  validation {
    condition     = var.monitoring_config.error_rate_threshold_pct > 0 && var.monitoring_config.error_rate_threshold_pct <= 100
    error_message = "monitoring_config.error_rate_threshold_pct is a percentage: above 0, at most 100."
  }
}

variable "ingress_gateway_service_name" {
  type        = string
  default     = ""
  description = "Cloud Run service of the Envoy ingress gateway (envoy-gateway-<env>) when it is enabled; empty skips the gateway 5xx and token-fetch alerts"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Target deployment region"
}

variable "subnet_self_link" {
  type        = string
  default     = ""
  description = "The self-link of the Shared VPC subnet to connect the Central Agent Gateway PSC Network Attachment"
}

variable "net_host_project_id" {
  type        = string
  default     = ""
  description = "The GCP Project ID of the Shared VPC host project"
}

variable "vpc_name" {
  type        = string
  default     = ""
  description = "The name of the Shared VPC network"
}

variable "agent_invoker_sa_emails" {
  type        = list(string)
  default     = []
  description = "List of agent/invoker service accounts granted access to the Gateway CA certificate secret"
}

variable "agent_project_ids" {
  type        = list(string)
  default     = []
  description = "List of agent project IDs (A2A and Root) that run AI agents and Reasoning Engines."
}

variable "enable_agent_gateway" {
  type        = bool
  default     = false
  description = "Toggle to deploy the Central Agent Gateway in Governance"
}

variable "internal_root_ca_pem" {
  type        = string
  description = "PEM of the internal Root CA (*.esmeralda.internal), owned by layer 3 security"
}

