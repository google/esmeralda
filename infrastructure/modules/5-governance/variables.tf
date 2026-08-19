# Variables for Stage 5 Governance Stack
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

variable "enable_analytics_views" {
  type        = bool
  default     = false
  description = "Set to true after initial agent inference traffic has generated BigQuery log tables"
}
